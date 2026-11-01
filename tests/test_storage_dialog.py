"""Smoke tests for the Storage Analyzer dialog."""

from niruvi.ui.storage_dialog import StorageDialog


def test_dialog_scans_and_lists_apps(tmp_path, qapp):
    app = tmp_path / "Demo"
    app.mkdir()
    (app / "payload").write_bytes(b"x" * 128)

    dlg = StorageDialog([("Demo", str(app))], None)
    dlg._worker.wait(5000)
    qapp.processEvents()

    assert dlg._report is not None
    assert dlg._report.apps_total == 128
    assert dlg.table.rowCount() >= 1
    assert dlg.table.item(0, 0).text() == "Demo"

    dlg.close()


def test_dialog_handles_missing_paths(tmp_path, qapp):
    dlg = StorageDialog([("Ghost", str(tmp_path / "does-not-exist"))], None)
    dlg._worker.wait(5000)
    qapp.processEvents()

    assert dlg._report is not None
    assert dlg._report.apps_total == 0

    dlg.close()
