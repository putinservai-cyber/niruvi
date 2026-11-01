"""Regression tests for UpdateInstallWorker.rollback()."""

import shutil
from pathlib import Path

from niruvi.ui.update_wizard import UpdateInstallWorker


def _make_dirs(tmp_path: Path):
    dest = tmp_path / "app"
    dest.mkdir()
    (dest / "live.txt").write_text("new")
    backup = tmp_path / "app.bak"
    backup.mkdir()
    (backup / "old.txt").write_text("old")
    return dest, backup


class TestRollback:
    def test_no_backup_returns_false(self, tmp_path, qapp):
        dest = tmp_path / "app"
        dest.mkdir()
        worker = UpdateInstallWorker("dl", str(dest), "App")
        assert worker.rollback() is False
        assert dest.is_dir()

    def test_restores_backup_and_is_idempotent(self, tmp_path, qapp):
        dest, backup = _make_dirs(tmp_path)
        worker = UpdateInstallWorker("dl", str(dest), "App")
        worker._backup_dir = str(backup)

        assert worker.rollback() is True
        assert (dest / "old.txt").read_text() == "old"
        assert not (dest / "live.txt").exists()
        assert not (tmp_path / "app.rollback_failed").exists()

        # Second call must not raise nor destroy the restored install.
        assert worker.rollback() is True
        assert (dest / "old.txt").read_text() == "old"

    def test_failed_copy_preserves_live_install(self, tmp_path, qapp, monkeypatch):
        dest, backup = _make_dirs(tmp_path)
        worker = UpdateInstallWorker("dl", str(dest), "App")
        worker._backup_dir = str(backup)

        def boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(shutil, "copytree", boom)

        assert worker.rollback() is False
        # The original live install must still be present (moved back).
        assert dest.is_dir()
        assert (dest / "live.txt").read_text() == "new"
        assert not (tmp_path / "app.rollback_failed").exists()
