"""Tests for the App Info dialog (tabs, customization, permissions behavior).

These guard against the tab-pages block being accidentally nested inside the
tab-switch handler (the pages list would then be out of scope at runtime).
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QMessageBox

from niruvi.core.policy_store import PolicyStore

APP_INFO = {"version": "1.0", "name": "NiruviSmoke"}


@pytest.fixture
def clean_policy():
    """Keep per-app policy files from leaking between tests."""
    store = PolicyStore()
    store.delete("NiruviSmoke")
    yield store
    store.delete("NiruviSmoke")


@pytest.fixture
def dialog(qapp, tmp_path, clean_policy):
    from niruvi.ui.app_info_dialog import AppInfoDialog
    from niruvi.utils.theme_engine import init_theme

    app_dir = tmp_path / "NiruviSmoke.AppImage"
    app_dir.mkdir()
    info = dict(APP_INFO, path=str(app_dir))

    init_theme(qapp)
    dlg = AppInfoDialog("NiruviSmoke", info, None)
    yield dlg
    dlg.deleteLater()


class TestAppInfoTabs:
    def test_constructs_with_expected_tabs(self, dialog):
        from niruvi.ui.app_info_dialog import _TABS

        assert len(dialog.tab_buttons) == len(_TABS)

    def test_fits_on_a_1366x768_screen(self, dialog):
        assert dialog.minimumWidth() <= 600
        assert dialog.minimumHeight() <= 520

    def test_every_tab_switches_the_stack(self, dialog):
        for i, btn in enumerate(dialog.tab_buttons):
            btn.click()
            assert dialog.stack.currentIndex() == i
            assert dialog.stack.widget(i) is not None

    def test_only_clicked_tab_is_checked(self, dialog):
        dialog.tab_buttons[2].click()
        checked = [b.isChecked() for b in dialog.tab_buttons]
        assert checked.count(True) == 1
        assert checked[2] is True

    def test_customization_page_is_built(self, dialog):
        assert dialog.display_name_edit is not None
        assert dialog.env_table is not None


class TestCustomization:
    def test_environment_variables_round_trip(self, dialog):
        from niruvi.desktop.installation_registry import InstallationRegistry

        dialog._add_env_row()
        dialog.env_table.item(0, 0).setText("NIRUVI_TEST_KEY")
        dialog.env_table.item(0, 1).setText("value")
        dialog._save_env_vars()

        record = InstallationRegistry().get("NiruviSmoke")
        assert record is not None
        assert record.env_vars == {"NIRUVI_TEST_KEY": "value"}

    def test_blank_env_rows_are_ignored(self, dialog):
        from niruvi.desktop.installation_registry import InstallationRegistry

        dialog._add_env_row()
        dialog.env_table.item(0, 0).setText("   ")
        dialog._save_env_vars()
        record = InstallationRegistry().get("NiruviSmoke")
        assert not (record and record.env_vars)

    def test_reset_customization_clears_only_customization(self, dialog, monkeypatch):
        from niruvi.desktop.installation_registry import InstallationRegistry

        record = InstallationRegistry().get("NiruviSmoke")
        if record is None:
            dialog._save_display_name()
            record = InstallationRegistry().get("NiruviSmoke")
        record.display_name_override = "Custom"
        record.run_args = "--verbose"
        record.env_vars = {"A": "B"}
        record.update_url = "https://github.com/example/app"
        InstallationRegistry().add(record)

        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
        dialog._reset_customization()

        updated = InstallationRegistry().get("NiruviSmoke")
        assert updated.display_name_override == ""
        assert updated.run_args == ""
        assert updated.env_vars == {}
        assert dialog.display_name_edit.text() == ""
        assert dialog.env_table.rowCount() == 0
        assert updated.update_url == "https://github.com/example/app"

    def test_reset_customization_respects_cancel(self, dialog, monkeypatch):
        from niruvi.desktop.installation_registry import InstallationRegistry

        record = InstallationRegistry().get("NiruviSmoke")
        if record is None:
            dialog._save_display_name()
            record = InstallationRegistry().get("NiruviSmoke")
        record.display_name_override = "Custom"
        InstallationRegistry().add(record)

        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
        dialog._reset_customization()

        updated = InstallationRegistry().get("NiruviSmoke")
        assert updated.display_name_override == "Custom"


class TestPermissions:
    def test_permissions_page_is_built(self, dialog):
        page = dialog._permissions_page
        assert page is not None
        assert len(page._entries) >= 15

    def test_categories_present(self, dialog):
        page = dialog._permissions_page
        assert page._home_entry is not None
        assert set(page._device_entries) == {"gpu", "audio", "microphone", "camera", "usb", "input"}
        assert "wayland" in page._ipc_entries

    def test_unsupported_controls_are_disabled_not_fake(self, dialog):
        """Shield cannot enforce device caps: the switch must be off/disabled."""
        page = dialog._permissions_page
        backend_name = page._backend_combo.currentData()
        if backend_name != "shield":
            pytest.skip("shield backend not the fallback here")
        entry = page._device_entries["gpu"]
        assert entry.isEnabled() is False
        # Should indicate not supported/enabled
        text = entry.state_label.text().lower()
        assert "not supported" in text or "not enforced" in text or "enable isolation" in text

    def test_apply_writes_policy_store(self, dialog, monkeypatch):
        _store = PolicyStore()
        page = dialog._permissions_page
        page._network_toggle.setChecked(False)
        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Apply))
        page._apply()

        policy = _store.load("NiruviSmoke")
        assert policy.network is False
        assert policy.enabled is True

    def test_discard_restores_saved_policy(self, dialog, monkeypatch):
        page = dialog._permissions_page
        page._network_toggle.setChecked(False)
        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Apply))
        page._apply()

        # Change again, then discard back to the applied state.
        page._home_combo.setCurrentIndex(1)  # Read-only
        page._discard()
        assert page._collect().network is False
        assert page._collect().home_access == "rw"

    def test_saved_unavailable_backend_falls_back(self, dialog):
        from niruvi.desktop.installation_registry import InstallationRegistry

        record = InstallationRegistry().get("NiruviSmoke")
        if record is None:
            dialog._save_display_name()
            record = InstallationRegistry().get("NiruviSmoke")
        record.sandbox_config = {"enabled": True, "backend": "firejail"}
        InstallationRegistry().add(record)

        # Existing policy file? none, so the mirror path is used.
        page = dialog._permissions_page
        if page._backend_combo.findData("firejail") >= 0:
            pytest.skip("firejail installed on this machine")
        page2 = type(page)("NiruviSmoke", dict(APP_INFO, path=dialog._info.get("path")))
        assert page2._backend_combo.currentData() == "shield"
        page2.deleteLater()
        dialog._permissions_page = page

    def test_enforcement_label_derived_from_backend(self, dialog):
        page = dialog._permissions_page
        text = page._enforcement_label.text()
        assert text
        assert "shield" in text.lower()

    def test_reset_defaults_loads_preset_for_review(self, dialog, monkeypatch):
        page = dialog._permissions_page
        # Private /tmp is off by default: enable+apply it, then verify reset
        # proposes defaults for review (write only happens on Apply).
        page._private_tmp_toggle.setChecked(True)
        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Apply))
        page._apply()
        assert page._collect().private_tmp is True

        page._reset_defaults()
        assert page._collect().private_tmp is False
        assert page._btn_apply.isEnabled()


class TestFileTree:
    @pytest.fixture
    def tree(self, qapp, tmp_path):
        from niruvi.ui.app_info_dialog import FileTreeWidget
        from niruvi.utils.theme_engine import init_theme

        init_theme(qapp)
        root = tmp_path / "app"
        root.mkdir()
        (root / "top.txt").write_text("hello")
        sub = root / "sub"
        sub.mkdir()
        (sub / "inner.txt").write_text("world")
        empty = root / "empty"
        empty.mkdir()
        widget = FileTreeWidget(str(root))
        yield widget
        widget.deleteLater()

    def test_columns_include_name_size_type(self, tree):
        headers = [tree.headerItem().text(i) for i in range(tree.columnCount())]
        assert headers[:3] == ["Name", "Size", "Type"]

    def test_only_top_level_is_read_up_front(self, tree):
        assert tree.topLevelItemCount() == 3

    def test_folder_children_load_lazily_on_expand(self, tree):
        from PyQt6.QtCore import Qt

        sub_item = next(
            tree.topLevelItem(i) for i in range(tree.topLevelItemCount()) if tree.topLevelItem(i).text(0) == "sub/"
        )
        assert sub_item.childCount() == 1
        assert sub_item.child(0).data(0, Qt.ItemDataRole.UserRole) is None

        tree.expandItem(sub_item)
        assert sub_item.childCount() == 1
        assert sub_item.child(0).text(0) == "inner.txt"

    def test_empty_folder_shows_marker(self, tree):
        empty_item = next(
            tree.topLevelItem(i) for i in range(tree.topLevelItemCount()) if tree.topLevelItem(i).text(0) == "empty/"
        )
        tree.expandItem(empty_item)
        assert empty_item.childCount() == 1
        assert empty_item.child(0).text(0) == "(empty)"

    def test_files_show_type_and_size(self, tree):
        file_item = next(
            tree.topLevelItem(i) for i in range(tree.topLevelItemCount()) if tree.topLevelItem(i).text(0) == "top.txt"
        )
        assert file_item.text(2) == "File"
        assert file_item.text(1)

    def test_refresh_rebuilds_tree(self, tree, tmp_path):
        (tmp_path / "app" / "added.txt").write_text("new")
        tree.refresh()
        names = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]
        assert "added.txt" in names
