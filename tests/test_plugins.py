"""Tests for the plugin system — drop-in event plugins and EventPlugin
entry-point integration."""

import stat
import sys

import pytest

import niruvi.core.plugins as plugins_mod
from niruvi.core.plugin import EventPlugin, register_plugin
from niruvi.core.plugins import EVENTS, emit, get_event_handlers, reload_plugins


@pytest.fixture()
def plugin_dir(tmp_path, monkeypatch):
    target = tmp_path / "plugins"
    target.mkdir()
    monkeypatch.setattr(plugins_mod, "PLUGINS_DIR", str(target))
    monkeypatch.setattr(plugins_mod, "_plugins", {})
    monkeypatch.setattr(plugins_mod, "_entry_handlers", {})
    monkeypatch.setattr(plugins_mod, "_entry_initialized", False)
    return target


def _write_plugin(plugin_dir, name, content, mode=0o600):
    path = plugin_dir / f"{name}.py"
    path.write_text(content)
    path.chmod(mode)
    return path


class TestDropInPlugins:
    def test_events_defined(self):
        for event in (
            "app_installed",
            "app_removed",
            "app_updated",
            "app_launched",
            "scan_completed",
            "update_available",
            "settings_changed",
        ):
            assert event in EVENTS

    def test_declarative_handlers(self, plugin_dir):
        _write_plugin(
            plugin_dir,
            "decl",
            "def on_launch(**kw):\n    global C;\n    C = kw['app_name']\n\nhandlers = {'app_launched': on_launch}\n",
        )
        reload_plugins()
        emit("app_launched", app_name="VLC")
        decl = sys.modules.get("_niruvi_plugin_decl")
        assert decl is not None
        assert decl.C == "VLC"

    def test_register_api(self, plugin_dir):
        _write_plugin(
            plugin_dir,
            "apiplugin",
            "def register(api):\n"
            "    def h(**kw):\n"
            "        global C;\n"
            "        C = kw['app_name']\n"
            "    api.on('app_updated', h)\n",
        )
        reload_plugins()
        emit("app_updated", app_name="X", old_version="1", new_version="2")
        mod = sys.modules.get("_niruvi_plugin_apiplugin")
        assert mod is not None
        assert mod.C == "X"

    def test_exception_isolated(self, plugin_dir):
        _write_plugin(
            plugin_dir,
            "boom",
            "handlers = {'app_installed': lambda **kw: (_ for _ in ()).throw(RuntimeError('boom'))}\n",
        )
        _write_plugin(
            plugin_dir,
            "fine",
            "def h(**kw):\n    global C;\n    C = kw['app_name']\n\nhandlers = {'app_installed': h}\n",
        )
        names = reload_plugins()
        assert "boom" in names and "fine" in names
        emit("app_installed", app_name="Y")
        fine = sys.modules.get("_niruvi_plugin_fine")
        assert fine.C == "Y"

    def test_world_writable_skipped(self, plugin_dir):
        _write_plugin(
            plugin_dir, "evil", "handlers = {'app_launched': lambda **kw: None}\n", mode=stat.S_IRWXU | stat.S_IWOTH
        )
        assert reload_plugins() == []

    def test_unknown_event_warns(self, plugin_dir, caplog):
        caplog.set_level("WARNING")
        emit("nonsense_event")
        assert any("Unknown plugin event" in r.message for r in caplog.records)

    def test_subdirectory_package(self, plugin_dir):
        sub = plugin_dir / "pkgdir"
        sub.mkdir()
        (sub / "__init__.py").write_text("handlers = {'scan_completed': lambda **kw: None}\n")
        assert reload_plugins() == ["pkgdir"]

    def test_list_plugins(self, plugin_dir):
        _write_plugin(plugin_dir, "a", "handlers = {'app_launched': lambda **kw: None}\n")
        _write_plugin(plugin_dir, "b", "handlers = {'app_removed': lambda **kw: None}\n")
        reload_plugins()
        assert sorted(plugins_mod.list_plugins()) == ["a", "b"]


class TestEntryPointPlugins:
    def test_event_plugin_dispatch(self, plugin_dir):
        received = {}

        class DemoEventPlugin(EventPlugin):
            plugin_name = "demo-entry"
            plugin_version = "0.1"

            def event_handlers(self):
                return {"app_launched": lambda **kw: received.update(kw)}

        register_plugin(DemoEventPlugin(), "event")
        reload_plugins()
        assert "demo-entry" in plugins_mod.list_plugins()
        emit("app_launched", app_name="X")
        assert received == {"app_name": "X"}

    def test_get_event_handlers_shape(self, plugin_dir):
        _write_plugin(plugin_dir, "shp", "handlers = {'app_launched': lambda **kw: None}\n")
        reload_plugins()
        mapping = get_event_handlers()
        assert "shp" in mapping
        assert callable(mapping["shp"]["app_launched"][0])
