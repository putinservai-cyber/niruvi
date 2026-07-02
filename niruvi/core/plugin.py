"""Plugin System — extension framework for Niruvi.

Defines abstract base classes for plugins and provides discovery,
registration, and lifecycle management. Supports builder plugins,
compressor plugins, theme plugins, and more.
"""

import importlib
import importlib.metadata
import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class PluginError(Exception):
    """Raised when a plugin operation fails."""


class Plugin(ABC):
    """Base class for all Niruvi plugins."""

    @property
    @abstractmethod
    def plugin_name(self) -> str:
        """Human-readable plugin name."""

    @property
    @abstractmethod
    def plugin_version(self) -> str:
        """Plugin version string."""

    @property
    def plugin_description(self) -> str:
        return ""

    def on_load(self):
        """Called after the plugin is loaded and registered."""

    def on_unload(self):
        """Called before the plugin is unregistered."""


class BuilderPlugin(Plugin):
    """Extend the build pipeline with custom steps."""

    @abstractmethod
    def build_step(self, appdir: str, config: dict[str, Any]) -> None:
        """Execute a build step. Receives the AppDir path and build config."""


class CompressorPlugin(Plugin):
    """Provide a custom compression backend for AppImage payloads."""

    @property
    @abstractmethod
    def compressor_name(self) -> str:
        """Short identifier like 'squashfs' or 'dwarfs'."""

    @abstractmethod
    def compress(self, source_dir: str, output_path: str) -> None:
        """Compress source_dir into output_path."""

    @abstractmethod
    def decompress(self, archive_path: str, output_dir: str) -> None:
        """Extract archive to output_dir."""


class ThemePlugin(Plugin):
    """Provide custom theme stylesheets and assets."""

    @property
    @abstractmethod
    def theme_name(self) -> str:
        """Theme identifier."""

    @abstractmethod
    def get_stylesheet(self, dark: bool = False) -> str:
        """Return QSS stylesheet for the theme."""


class InstallerPlugin(Plugin):
    """Customize or extend the installation wizard."""

    @abstractmethod
    def on_install_start(self, config: dict[str, Any]) -> None:
        """Called when installation begins."""

    @abstractmethod
    def on_install_finish(self, dest: str) -> None:
        """Called after installation completes."""


class UpdaterPlugin(Plugin):
    """Provide a custom update checking/resolution strategy."""

    @abstractmethod
    def check_update(self, current_version: str, update_url: str) -> dict[str, Any] | None:
        """Check for updates. Return manifest dict or None."""


class SigningPlugin(Plugin):
    """Provide code signing and verification."""

    @abstractmethod
    def sign(self, file_path: str) -> bytes:
        """Sign a file, return signature bytes."""

    @abstractmethod
    def verify(self, file_path: str, signature: bytes) -> bool:
        """Verify a signature against a file."""


_PLUGIN_REGISTRY: dict[str, list[Plugin]] = {
    "builder": [],
    "compressor": [],
    "theme": [],
    "installer": [],
    "updater": [],
    "signing": [],
}


def register_plugin(plugin: Plugin, category: str):
    if category not in _PLUGIN_REGISTRY:
        raise PluginError(f"Unknown plugin category: {category}")
    _PLUGIN_REGISTRY[category].append(plugin)
    try:
        plugin.on_load()
    except Exception as e:
        logger.warning("Plugin on_load failed for %s: %s", plugin.plugin_name, e)
    logger.info("Registered plugin: %s v%s in %s", plugin.plugin_name, plugin.plugin_version, category)


def unregister_plugin(plugin: Plugin, category: str):
    if category not in _PLUGIN_REGISTRY:
        return
    try:
        plugin.on_unload()
    except Exception as e:
        logger.warning("Plugin on_unload failed for %s: %s", plugin.plugin_name, e)
    _PLUGIN_REGISTRY[category] = [p for p in _PLUGIN_REGISTRY[category] if p is not plugin]


def get_plugins(category: str) -> list[Plugin]:
    return list(_PLUGIN_REGISTRY.get(category, []))


def discover_entry_point_plugins():
    """Discover plugins via setuptools entry_points (niruvi.plugins)."""
    for entry_point in importlib.metadata.entry_points(group="niruvi.plugins"):
        try:
            plugin_class = entry_point.load()
            category = getattr(plugin_class, "plugin_category", "builder")
            instance = plugin_class()
            register_plugin(instance, category)
            logger.info("Discovered plugin: %s from %s", entry_point.name, entry_point.module)
        except Exception as e:
            logger.warning("Failed to load plugin %s: %s", entry_point.name, e)


def discover_builtin_plugins():
    """Register built-in plugins that ship with Niruvi."""
    pass


def init_plugins():
    discover_entry_point_plugins()
    discover_builtin_plugins()
    logger.info("Plugin system initialized with %d total plugins",
                sum(len(v) for v in _PLUGIN_REGISTRY.values()))
