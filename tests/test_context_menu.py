"""Tests for the per-app context menu structure and dispatch.

The menu is built by ``_build_app_context_menu`` (pure, testable) and executed
by ``_show_context_menu``. Actions carry string keys so dispatch is stable.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from niruvi._version import __app_name__


@pytest.fixture
def app_window(qapp):
    from niruvi.ui.manager import Niruvi
    from niruvi.utils.theme_engine import init_theme

    init_theme(qapp)
    window = Niruvi()
    yield window
    window.close()
    window.deleteLater()


def _submenus(menu):
    return {action.text(): action.menu() for action in menu.actions() if action.menu()}


def _keys(menu):
    return [action.data() for action in menu.actions() if action.data()]


class TestContextMenuStructure:
    def test_unknown_app_has_no_menu(self, app_window):
        assert app_window._build_app_context_menu("DoesNotExist") is None

    def test_top_level_actions_are_grouped(self, app_window):
        app_window.installed_apps["Fake"] = {"path": "/tmp/fake", "version": "1.0", "update_url": ""}
        menu = app_window._build_app_context_menu("Fake")
        keys = _keys(menu)
        assert "info" in keys
        assert "run" in keys
        assert keys[-1] == "uninstall", "uninstall must be the last, separated action"

    def test_manage_and_desktop_submenus_exist(self, app_window):
        app_window.installed_apps["Fake"] = {"path": "/tmp/fake", "version": "1.0", "update_url": ""}
        subs = _submenus(app_window._build_app_context_menu("Fake"))
        assert "Manage" in subs
        assert "Desktop" in subs
        manage_keys = _keys(subs["Manage"])
        assert "update" in manage_keys
        assert "verify" in manage_keys
        assert "repair" in manage_keys
        desktop_keys = _keys(subs["Desktop"])
        assert "open_folder" in desktop_keys
        assert "shortcut" in desktop_keys

    def test_run_hidden_for_niruvi_itself(self, app_window):
        app_window.installed_apps[__app_name__] = {"path": "/tmp/self", "version": "1.0"}
        keys = _keys(app_window._build_app_context_menu(__app_name__))
        assert "run" not in keys
        assert "run_unsandboxed" not in keys

    def test_check_update_only_when_url_present(self, app_window):
        app_window.installed_apps["NoUrl"] = {"path": "/tmp/fake", "version": "1.0", "update_url": ""}
        subs = _submenus(app_window._build_app_context_menu("NoUrl"))
        assert "check_update" not in _keys(subs["Manage"])

        app_window.installed_apps["WithUrl"] = {"path": "/tmp/fake", "version": "1.0", "update_url": "https://x"}
        subs = _submenus(app_window._build_app_context_menu("WithUrl"))
        assert "check_update" in _keys(subs["Manage"])

    def test_shortcut_label_reflects_state(self, app_window):
        app_window.installed_apps["NoShortcut"] = {"path": "/tmp/fake", "version": "1.0"}
        desktop = _submenus(app_window._build_app_context_menu("NoShortcut"))["Desktop"]
        texts = [a.text() for a in desktop.actions()]
        assert "Create Desktop Shortcut" in texts

        app_window.installed_apps["HasShortcut"] = {
            "path": "/tmp/fake",
            "version": "1.0",
            "desktop_shortcut": "/x.desktop",
        }
        desktop = _submenus(app_window._build_app_context_menu("HasShortcut"))["Desktop"]
        texts = [a.text() for a in desktop.actions()]
        assert "Remove Desktop Shortcut" in texts


class TestContextDispatch:
    def test_uninstall_key_routes_to_uninstall(self, app_window, monkeypatch):
        calls = {}
        monkeypatch.setattr(app_window, "_uninstall_app", lambda name: calls.setdefault("uninstall", name))
        app_window.installed_apps["Fake"] = {"path": "/tmp/fake", "version": "1.0"}
        app_window._dispatch_context_action("Fake", "uninstall")
        assert calls["uninstall"] == "Fake"

    def test_shortcut_key_toggles_based_on_state(self, app_window, monkeypatch):
        calls = {}
        monkeypatch.setattr(app_window, "_create_desktop_shortcut", lambda name: calls.setdefault("created", name))
        monkeypatch.setattr(app_window, "_remove_desktop_shortcut", lambda name: calls.setdefault("removed", name))

        app_window.installed_apps["Fake"] = {"path": "/tmp/fake", "version": "1.0"}
        app_window._dispatch_context_action("Fake", "shortcut")
        assert calls.get("created") == "Fake"

        app_window.installed_apps["Fake"]["desktop_shortcut"] = "/x.desktop"
        app_window._dispatch_context_action("Fake", "shortcut")
        assert calls.get("removed") == "Fake"
