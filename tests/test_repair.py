"""Tests for the Repair Engine."""

import os
import stat
import tempfile

import pytest


def _isolate_home(monkeypatch, tmp_path):
    """Redirect ~ to a temp dir so repair tests never touch the real user home."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(fake_home) + p[1:])
    import niruvi.config as config

    fake_apps = str(fake_home / ".local" / "share" / "applications")
    fake_install = str(fake_home / "Applications")
    monkeypatch.setattr(config, "DEFAULT_INSTALL_DIR", fake_install)
    monkeypatch.setattr(config, "DESKTOP_DIR", fake_apps)
    monkeypatch.setattr(config, "INSTALLED_DIR", str(fake_home / "Applications" / "Niruvi"))
    import niruvi.desktop.desktop_utils as desktop_utils

    monkeypatch.setattr(desktop_utils, "DESKTOP_DIR", fake_apps)
    import niruvi.desktop.thumbnailer as thumbnailer

    fake_thumb = str(fake_home / ".local" / "share" / "thumbnailers")
    fake_bin = str(fake_home / ".local" / "bin")
    monkeypatch.setattr(thumbnailer, "THUMBNAILER_DIR", fake_thumb)
    monkeypatch.setattr(thumbnailer, "THUMBNAILER_FILE", os.path.join(fake_thumb, "niruvi-appimage.thumbnailer"))
    monkeypatch.setattr(thumbnailer, "HELPER_DIR", fake_bin)
    monkeypatch.setattr(thumbnailer, "HELPER_PATH", os.path.join(fake_bin, "niruvi-thumbnailer"))


from niruvi.core.repair import RepairAction, RepairReport, repair_apprun


class TestRepairAction:
    def test_success(self):
        action = RepairAction("test", lambda: True)
        assert action.execute() is True
        assert action.success is True
        assert action.error is None

    def test_failure(self):
        action = RepairAction("test", lambda: False)
        assert action.execute() is False
        assert action.success is False

    def test_exception(self):
        def _fail():
            raise ValueError("oops")

        action = RepairAction("test", _fail)
        assert action.execute() is False
        assert action.error == "oops"


class TestRepairReport:
    def test_empty(self):
        report = RepairReport()
        assert report.success_count == 0
        assert report.failure_count == 0
        assert report.all_succeeded is True

    def test_mixed_results(self):
        report = RepairReport()
        report.add(RepairAction("ok", lambda: True))
        report.add(RepairAction("fail", lambda: False))
        for a in report.actions:
            a.execute()
        assert report.success_count == 1
        assert report.failure_count == 1
        assert report.all_succeeded is False

    def test_summary(self):
        report = RepairReport()
        report.add(RepairAction("ok", lambda: True))
        report.add(RepairAction("ok2", lambda: True))
        for a in report.actions:
            a.execute()
        s = report.summary()
        assert "2 succeeded" in s
        assert "0 failed" in s


class TestRepairAppRun:
    def test_fixes_permissions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            apprun = os.path.join(tmpdir, "AppRun")
            with open(apprun, "w") as f:
                f.write("#!/bin/bash")
            os.chmod(apprun, 0o644)
            action = repair_apprun(tmpdir)
            assert action.execute() is True
            mode = os.stat(apprun).st_mode
            assert mode & stat.S_IXUSR
            assert mode & stat.S_IXGRP
            assert mode & stat.S_IXOTH

    def test_missing_apprun(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            action = repair_apprun(tmpdir)
            assert action.execute() is False

    def test_already_executable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            apprun = os.path.join(tmpdir, "AppRun")
            with open(apprun, "w") as f:
                f.write("#!/bin/bash")
            os.chmod(apprun, 0o755)
            action = repair_apprun(tmpdir)
            assert action.execute() is True


class TestJunestRelocation:
    @pytest.fixture(autouse=True)
    def _no_real_home(self, monkeypatch, tmp_path):
        _isolate_home(monkeypatch, tmp_path)

    def _broken_app(self, tmp_path):
        app = tmp_path / "VLC media player"
        (app / ".junest").mkdir(parents=True)
        (app / "AppRun").write_text("#!/bin/sh\necho vlc\n")
        os.chmod(os.path.join(app, "AppRun"), 0o755)
        (app / "DirIcon.png").write_bytes(b"\x89PNG\r\n")
        return app

    def test_relocation_moves_and_cleans_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NIRUVI_DATA_DIR", str(tmp_path / "data"))
        from niruvi.core.repair import repair_junest_relocation

        app = self._broken_app(tmp_path)
        action = repair_junest_relocation("VLC media player", str(app))
        assert action.execute() is True
        target = str(tmp_path / "VLC-media-player")
        assert action.new_path == target
        assert not os.path.exists(str(app))
        assert os.path.isdir(os.path.join(target, ".junest"))
        assert os.path.isfile(os.path.join(target, "AppRun"))

    def test_noop_on_plain_app(self, tmp_path):
        from niruvi.core.repair import repair_junest_relocation

        app = tmp_path / "Plain App"
        app.mkdir()
        action = repair_junest_relocation("Plain App", str(app))
        assert action.execute() is False

    def test_noop_on_spacefree_junest(self, tmp_path):
        from niruvi.core.repair import repair_junest_relocation

        app = tmp_path / "vlc-media"
        (app / ".junest").mkdir(parents=True)
        action = repair_junest_relocation("vlc-media", str(app))
        assert action.execute() is False

    def test_target_exists_fails(self, tmp_path):
        from niruvi.core.repair import repair_junest_relocation

        app = self._broken_app(tmp_path)
        (tmp_path / "VLC-media-player").mkdir()
        action = repair_junest_relocation("VLC media player", str(app))
        assert action.execute() is False
        assert "already exists" in action.error

    def test_registry_updated(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NIRUVI_DATA_DIR", str(tmp_path / "data"))
        from niruvi.core.repair import repair_junest_relocation
        from niruvi.desktop.installation_registry import InstallationRecord, InstallationRegistry

        app = self._broken_app(tmp_path)
        reg = InstallationRegistry()
        reg.add(InstallationRecord(name="VLC media player", path=str(app), version="3.0.20"))
        reg.flush()
        action = repair_junest_relocation("VLC media player", str(app))
        assert action.execute() is True
        record = reg.get("VLC media player")
        assert record is not None
        assert record.path == str(tmp_path / "VLC-media-player")

    def test_repair_full_includes_relocation(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NIRUVI_DATA_DIR", str(tmp_path / "data"))
        from niruvi.core.repair import repair_full
        from niruvi.desktop.installation_registry import InstallationRecord, InstallationRegistry

        app = self._broken_app(tmp_path)
        reg = InstallationRegistry()
        reg.add(InstallationRecord(name="VLC media player", path=str(app), version="3.0.20"))
        reg.flush()
        report = repair_full("VLC media player", str(app))
        assert report.all_succeeded is True
        record = reg.get("VLC media player")
        assert record.path == str(tmp_path / "VLC-media-player")
        assert os.path.isdir(os.path.join(record.path, ".junest"))

    def test_repair_full_plain_app_no_relocation(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NIRUVI_DATA_DIR", str(tmp_path / "data"))
        from niruvi.core.repair import repair_full

        app = tmp_path / "Plain"
        app.mkdir()
        (app / "AppRun").write_text("#!/bin/sh\n")
        os.chmod(os.path.join(app, "AppRun"), 0o755)
        (app / "DirIcon.png").write_bytes(b"\x89PNG\r\n")
        report = repair_full("Plain", str(app))
        assert report.all_succeeded is True
