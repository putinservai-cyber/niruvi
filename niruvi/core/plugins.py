"""Python plugin API for Niruvi.

Plugins are plain Python modules placed in ``~/.config/niruvi/plugins``
(or a subdirectory containing an ``__init__.py``). A plugin declares
interest in events either declaratively:

    handlers = {
        "app_installed": my_install_handler,
        "app_launched": my_launch_handler,
    }

or imperatively via a ``register(api)`` function:

    def register(api):
        api.on("app_updated", handle_update)

Handlers receive keyword arguments describing the event (see ``EVENTS``
below). Exceptions raised by plugins are logged and never propagate.

Security: plugin files must be owned by the current user and must not be
world-writable; anything else is skipped with a warning (same policy as
the script hooks system).
"""

import importlib.util
import logging
import os
import stat
import sys

logger = logging.getLogger(__name__)

PLUGINS_DIR = os.path.expanduser("~/.config/niruvi/plugins")

#: Supported events: name -> description of keyword arguments.
EVENTS = {
    "app_installed": "app_name, app_dir, version",
    "app_removed": "app_name, app_dir",
    "app_updated": "app_name, old_version, new_version",
    "app_launched": "app_name, app_dir",
    "scan_completed": "app_count",
    "update_available": "app_name, version, source_type",
    "settings_changed": "keys",
}

_plugins: dict[str, dict] = {}
_entry_handlers: dict[str, list] = {}
_entry_initialized = False


class _PluginAPI:
    """Small API object handed to a plugin's ``register()`` function."""

    def __init__(self, handlers: dict):
        self._handlers = handlers

    def on(self, event: str, fn):
        self._handlers.setdefault(event, []).append(fn)


def get_plugins_dir() -> str:
    return PLUGINS_DIR


def ensure_plugins_dir() -> str:
    os.makedirs(PLUGINS_DIR, exist_ok=True)
    return PLUGINS_DIR


def _check_plugin_secure(path: str) -> bool:
    try:
        st = os.stat(path)
        if st.st_uid != os.getuid():
            logger.warning("Plugin %s is not owned by current user — skipping", path)
            return False
        if st.st_mode & stat.S_IWOTH:
            logger.warning("Plugin %s is world-writable — skipping", path)
            return False
        return True
    except OSError as e:
        logger.warning("Cannot stat plugin %s: %s — skipping", path, e)
        return False


def _discover_plugin_files() -> list[str]:
    """Return plugin module paths found under PLUGINS_DIR."""
    files = []
    if not os.path.isdir(PLUGINS_DIR):
        return files
    real_base = os.path.realpath(PLUGINS_DIR)
    for entry in sorted(os.listdir(PLUGINS_DIR)):
        if entry.startswith("_") or entry.startswith("."):
            continue
        path = os.path.join(PLUGINS_DIR, entry)
        real_path = os.path.realpath(path)
        if not real_path.startswith(real_base + os.sep):
            logger.warning("Plugin %s escapes plugins directory — skipping", path)
            continue
        if entry.endswith(".py") and os.path.isfile(path):
            files.append(path)
        elif os.path.isdir(path):
            init = os.path.join(path, "__init__.py")
            if os.path.isfile(init):
                files.append(init)
    return files


def _collect_handlers(module, plugin_name: str) -> dict:
    handlers: dict = {}
    declared = getattr(module, "handlers", None)
    if isinstance(declared, dict):
        for event, fn in declared.items():
            handlers.setdefault(event, []).append(fn)
    register_fn = getattr(module, "register", None)
    if callable(register_fn):
        try:
            api = _PluginAPI(handlers)
            register_fn(api)
        except Exception as e:
            logger.warning("Plugin %s register() failed: %s", plugin_name, e)
    return handlers


def reload_plugins() -> list[str]:
    """Load (or reload) all plugins and return the active plugin names."""
    global _plugins, _entry_handlers, _entry_initialized
    _plugins = {}
    _entry_handlers = {}
    names = []
    if not _entry_initialized:
        try:
            from niruvi.core.plugin import init_plugins

            init_plugins()
            _entry_initialized = True
        except Exception as e:
            logger.warning("Entry-point plugin discovery failed: %s", e)
    try:
        from niruvi.core.plugin import get_plugins

        for entry_plugin in get_plugins("event"):
            try:
                handlers = entry_plugin.event_handlers() or {}
                normalized = {ev: ([fn] if callable(fn) else list(fn)) for ev, fn in handlers.items()}
                if normalized:
                    _entry_handlers[entry_plugin.plugin_name] = normalized
            except Exception as e:
                logger.warning("EventPlugin %s failed: %s", entry_plugin.plugin_name, e)
    except Exception as e:
        logger.warning("Event plugin collection failed: %s", e)
    for plugin_name, handlers in _entry_handlers.items():
        _plugins[plugin_name] = handlers
        names.append(plugin_name)
    for path in _discover_plugin_files():
        real_path = os.path.realpath(path)
        if not _check_plugin_secure(real_path):
            continue
        plugin_name = os.path.splitext(os.path.basename(real_path))[0]
        dir_name = os.path.basename(os.path.dirname(real_path))
        if dir_name != "plugins":
            if plugin_name == "__init__":
                plugin_name = dir_name
            else:
                plugin_name = f"{dir_name}_{plugin_name}"
        module_name = f"_niruvi_plugin_{plugin_name}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, real_path)
            if spec is None or spec.loader is None:
                logger.warning("Cannot create spec for plugin %s", real_path)
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        except Exception as e:
            logger.warning("Failed to load plugin %s: %s", real_path, e)
            continue
        handlers = _collect_handlers(module, plugin_name)
        if handlers:
            _plugins[plugin_name] = handlers
            names.append(plugin_name)
            logger.info("Loaded plugin %s: %s", plugin_name, list(handlers))
    return names


def list_plugins() -> list[str]:
    return sorted(_plugins)


def emit(event: str, **kwargs):
    """Dispatch an event to all plugins that registered a handler for it."""
    if event not in EVENTS:
        logger.warning("Unknown plugin event: %s", event)
        return
    if not _plugins:
        return
    for plugin_name, handlers in _plugins.items():
        for fn in handlers.get(event, []):
            try:
                fn(**kwargs)
            except Exception as e:
                logger.warning("Plugin %s handler for %s failed: %s", plugin_name, event, e)


def get_event_handlers() -> dict:
    """Return the active {plugin_name: {event: [fn, ...]}} mapping (for tools/tests)."""
    return {name: dict(handlers) for name, handlers in _plugins.items()}
