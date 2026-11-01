"""Tests for the permission management window."""

import pytest
from PyQt6.QtWidgets import QDialog

import niruvi.ui.permissions_dialog as pd
from niruvi.core.policy_store import PolicyStore
from niruvi.ui.permissions_dialog import PermissionsWindow


@pytest.fixture
def apps(tmp_path):
    a = tmp_path / "Demo"
    a.mkdir()
    b = tmp_path / "Other"
    b.mkdir()
    return [
        {"id": "Demo", "name": "Demo", "path": str(a), "icon_path": ""},
        {"id": "Other", "name": "Other", "path": str(b), "icon_path": ""},
    ]


@pytest.fixture
def no_modal(monkeypatch):
    monkeypatch.setattr(pd.ChangePreviewDialog, "exec", lambda self: QDialog.DialogCode.Accepted)


class TestConstruction:
    def test_has_all_categories(self, qapp, apps):
        w = PermissionsWindow(apps)
        assert w.stack.count() == 11
        w.close()

    def test_selects_first_app(self, qapp, apps):
        w = PermissionsWindow(apps)
        assert w._current_id == "Demo"
        assert w.status_badge.text()
        w.close()

    def test_empty_apps_ok(self, qapp):
        w = PermissionsWindow([])
        assert w._current_id is None
        w.close()


class TestGating:
    def test_unsupported_controls_disabled(self, qapp, apps):
        w = PermissionsWindow(apps)
        # Niruvi Shield cannot enforce per-device filtering.
        assert w.dev_boxes["camera"].isEnabled() is False
        # Filesystem/environment are supported.
        assert w.fs_home.isEnabled() is True
        assert w.env_table.isEnabled() is True
        w.close()

    def test_support_labels_rendered(self, qapp, apps):
        w = PermissionsWindow(apps)
        assert "Supported" in w.fs_support.text() or "Partial" in w.fs_support.text()
        assert w.net_support.text()
        w.close()


class TestDirtyTracking:
    def test_toggle_marks_modified(self, qapp, apps):
        w = PermissionsWindow(apps)
        assert w._is_modified("Demo") is False
        w.net_enabled.setChecked(False)
        assert w._is_modified("Demo") is True
        assert w.btn_discard.isEnabled() is True
        w.close()

    def test_discard_reverts(self, qapp, apps):
        w = PermissionsWindow(apps)
        w.net_enabled.setChecked(False)
        w._discard()
        assert w._is_modified("Demo") is False
        assert w.net_enabled.isChecked() is True
        w.close()


class TestApply:
    def test_apply_persists_policy(self, qapp, apps, no_modal, monkeypatch):
        store = PolicyStore()
        w = PermissionsWindow(apps)
        w.net_enabled.setChecked(False)
        w.fs_home.setCurrentIndex(w.fs_home.findData("none"))
        w._apply()
        stored = store.load("Demo")
        assert stored.network is False
        assert stored.home_access == "none"
        assert w._is_modified("Demo") is False
        assert len(store.history("Demo")) >= 1
        w.close()

    def test_apply_rejects_invalid_env(self, qapp, apps, monkeypatch):
        errors = []
        monkeypatch.setattr(pd.QMessageBox, "warning", lambda *a, **k: errors.append(a))
        w = PermissionsWindow(apps)
        w.env_table.insertRow(0)
        from PyQt6.QtWidgets import QTableWidgetItem

        w.env_table.setItem(0, 0, QTableWidgetItem("bad name"))
        w.env_table.setItem(0, 1, QTableWidgetItem("x"))
        w._apply()
        assert errors
        w.close()

    def test_restore_previous_available_after_apply(self, qapp, apps, no_modal):
        w = PermissionsWindow(apps)
        w.net_enabled.setChecked(False)
        w._apply()
        assert w.btn_restore.isEnabled() is True
        w._restore_previous()
        assert w.net_enabled.isChecked() is True
        w.close()


class TestPresets:
    def test_preset_applies_after_preview(self, qapp, apps, no_modal):
        w = PermissionsWindow(apps)
        idx = w.preset_combo.findData("restricted")
        w.preset_combo.setCurrentIndex(idx)
        w._apply_preset()
        assert w.net_enabled.isChecked() is False
        assert w._is_modified("Demo") is True
        w.close()


class TestFiltering:
    def test_search_filters_list(self, qapp, apps):
        w = PermissionsWindow(apps)
        w.search.setText("Demo")
        assert w.app_list.count() == 1
        w.search.setText("")
        assert w.app_list.count() == 2
        w.close()

    def test_reset_default(self, qapp, apps):
        w = PermissionsWindow(apps)
        w.net_enabled.setChecked(False)
        w._reset_default()
        assert w.net_enabled.isChecked() is True
        w.close()
