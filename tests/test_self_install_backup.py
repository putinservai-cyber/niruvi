"""Regression tests for the self-installer rollback backup lifecycle.

Previously the previous installation was moved into a temp ``aim-rollback-*``
directory that was never removed, leaking a full copy of the old install.
"""

from niruvi.ui.self_installer_wizard import InstallWorker


def test_dispose_on_success_removes_backup(tmp_path, qapp):
    dest = tmp_path / "install"
    dest.mkdir()
    (dest / "new-file").write_text("new")
    backup = tmp_path / "aim-rollback-xyz"
    backup.mkdir()
    (backup / "old-file").write_text("old")

    worker = InstallWorker({}, str(dest), "")
    worker._backup = str(backup)

    worker._dispose_rollback_backup(success=True)

    assert not backup.exists()
    assert (dest / "new-file").exists()
    assert worker._backup is None


def test_dispose_on_failure_restores_previous_install(tmp_path, qapp):
    dest = tmp_path / "install"
    dest.mkdir()
    (dest / "partial").write_text("half-written")
    backup = tmp_path / "aim-rollback-xyz"
    backup.mkdir()
    (backup / "old-file").write_text("old")

    worker = InstallWorker({}, str(dest), "")
    worker._backup = str(backup)

    worker._dispose_rollback_backup(success=False)

    assert not (dest / "partial").exists()
    assert (dest / "old-file").read_text() == "old"
    assert not backup.exists()
    assert worker._backup is None


def test_dispose_without_backup_is_noop(tmp_path, qapp):
    dest = tmp_path / "install"
    dest.mkdir()
    worker = InstallWorker({}, str(dest), "")
    worker._dispose_rollback_backup(success=True)
    assert worker._backup is None
