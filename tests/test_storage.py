"""Tests for storage accounting and safe temp cleanup."""

import os
import time
from pathlib import Path

from niruvi.core.storage import (
    analyze_storage,
    app_dir_size,
    cleanup_obsolete_backups,
    cleanup_stale_temp_dirs,
    iter_temp_dirs,
)


def _write(path: Path, size: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


class TestAnalyzeStorage:
    def test_seed_images_not_double_counted(self, tmp_path):
        app = tmp_path / "Demo"
        _write(app / "bin" / "demo", 100)
        _write(app / "Demo.AppImage", 200)

        report = analyze_storage([("Demo", str(app))], temp_root=str(tmp_path / "none"))

        entry = report.apps[0]
        assert entry.app_files == 100  # seed excluded
        assert entry.seed_images == 200
        assert entry.total == 300  # not 500

    def test_app_dir_size_helper(self, tmp_path):
        app = tmp_path / "Demo"
        _write(app / "file", 100)
        _write(app / "Demo.AppImage", 200)

        assert app_dir_size(str(app)) == 100
        assert app_dir_size(str(app), include_seed=True) == 300

    def test_categories_accounted_separately(self, tmp_path):
        app = tmp_path / "Demo"
        _write(app / "file", 10)
        _write(Path(str(app) + ".home") / "data", 20)
        _write(Path(str(app) + ".config") / "cfg", 30)
        _write(Path(str(app) + ".prev") / "old", 40)
        _write(Path(str(app) + ".bak") / "stale", 50)

        report = analyze_storage([("Demo", str(app))], temp_root=str(tmp_path / "none"))
        entry = report.apps[0]
        assert entry.portable_home == 20
        assert entry.portable_config == 30
        assert entry.rollback == 40
        assert entry.obsolete == 50
        assert entry.total == 10 + 20 + 30 + 40 + 50

    def test_symlinks_not_counted_twice(self, tmp_path):
        app = tmp_path / "Demo"
        _write(app / "real", 100)
        os.symlink(app / "real", app / "link")

        report = analyze_storage([("Demo", str(app))], temp_root=str(tmp_path / "none"))
        assert report.apps[0].app_files == 100

    def test_grand_total_includes_temp(self, tmp_path):
        app = tmp_path / "Demo"
        _write(app / "file", 10)
        temp = tmp_path / "tmp"
        _write(temp / "niruvi-Demo-abc" / "x", 90)

        report = analyze_storage([("Demo", str(app))], temp_root=str(temp))
        assert report.apps_total == 10
        assert report.temp_total == 90
        assert report.grand_total == 100


class TestTempDirs:
    def test_only_niruvi_prefixes_listed(self, tmp_path):
        temp = tmp_path / "tmp"
        (temp / "niruvi-Demo-1").mkdir(parents=True)
        (temp / "aim-rollback-1").mkdir()
        (temp / "unrelated").mkdir()
        (temp / "regular-file").write_text("x")

        found = {os.path.basename(p) for p in iter_temp_dirs(str(temp))}
        assert found == {"niruvi-Demo-1", "aim-rollback-1"}

    def test_cleanup_removes_only_stale_niruvi_dirs(self, tmp_path):
        temp = tmp_path / "tmp"
        old = temp / "niruvi-Demo-old"
        _write(old / "payload", 500)
        fresh = temp / "niruvi-Demo-fresh"
        _write(fresh / "payload", 500)
        unrelated_old = temp / "other-old"
        _write(unrelated_old / "payload", 500)

        stale = time.time() - 3600
        os.utime(old, (stale, stale))
        os.utime(unrelated_old, (stale, stale))

        removed, freed = cleanup_stale_temp_dirs(max_age_seconds=600, temp_root=str(temp))

        assert removed == [str(old)]
        assert freed == 500
        assert not old.exists()
        assert fresh.exists()
        assert unrelated_old.exists()

    def test_cleanup_never_touches_active_paths(self, tmp_path):
        temp = tmp_path / "tmp"
        old = temp / "niruvi-Demo-active"
        _write(old / "payload", 10)
        stale = time.time() - 3600
        os.utime(old, (stale, stale))

        removed, _freed = cleanup_stale_temp_dirs(
            max_age_seconds=0, temp_root=str(temp), active_paths=[str(old)]
        )
        assert removed == []
        assert old.exists()

    def test_cleanup_never_touches_data_dir(self, tmp_path, monkeypatch):
        data_dir = tmp_path / "niruvi-data"
        _write(data_dir / "registry.db", 400)
        stale = time.time() - 3600
        os.utime(data_dir, (stale, stale))

        removed, _freed = cleanup_stale_temp_dirs(max_age_seconds=0, temp_root=str(tmp_path))

        assert removed == []
        assert (data_dir / "registry.db").exists()


class TestObsoleteBackups:
    def test_removes_leftovers_keeps_app_and_rollback(self, tmp_path):
        app = tmp_path / "Demo"
        _write(app / "keep", 10)
        _write(Path(str(app) + ".bak") / "stale", 100)
        _write(Path(str(app) + ".staging") / "stale", 100)
        _write(Path(str(app) + ".prev") / "rollback", 100)

        freed = cleanup_obsolete_backups("Demo", str(app))

        assert freed == 200
        assert app.exists()
        assert (Path(str(app) + ".prev") / "rollback").exists()
        assert not Path(str(app) + ".bak").exists()
        assert not Path(str(app) + ".staging").exists()
