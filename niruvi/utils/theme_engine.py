"""Theme Engine — detects system theme mode without overriding native styling.

The engine only reports whether the system is using a dark or light theme.
No custom palettes, no QSS — Qt uses whatever the desktop environment provides.
"""

import logging
from enum import Enum, auto

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

logger = logging.getLogger(__name__)


class ThemeMode(Enum):
    LIGHT = auto()
    DARK = auto()
    AUTO = auto()


class ThemeEngine:
    """Reports the current system theme mode.

    Does not apply any palettes or stylesheets — the native desktop
    theme (GTK, Breeze, Adwaita, etc.) handles all rendering.
    """

    def __init__(self):
        self._mode = ThemeMode.AUTO
        self._listeners: list[callable] = []

    @property
    def mode(self) -> ThemeMode:
        return self._mode

    @mode.setter
    def mode(self, value: ThemeMode):
        self._mode = value
        self._notify()

    @property
    def effective_mode(self) -> ThemeMode:
        if self._mode == ThemeMode.AUTO:
            return self._detect_system_theme()
        return self._mode

    def is_dark(self) -> bool:
        return self.effective_mode == ThemeMode.DARK

    def on_theme_changed(self, callback: callable):
        self._listeners.append(callback)

    def _notify(self):
        for cb in self._listeners:
            try:
                cb(self._mode)
            except Exception as e:
                logger.warning("Theme listener error: %s", e)

    def _detect_system_theme(self) -> ThemeMode:
        # GNOME / GTK
        try:
            import subprocess

            result = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if "dark" in result.stdout.lower():
                return ThemeMode.DARK
        except Exception:
            pass
        # KDE Plasma
        try:
            result = subprocess.run(
                ["kreadconfig6", "--group", "General", "--key", "ColorScheme", "--default", "Breeze"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if "dark" in result.stdout.lower():
                return ThemeMode.DARK
        except Exception:
            pass
        return ThemeMode.LIGHT

    def apply(self):
        """No-op — native theme is already active."""
        self._notify()


_engine: ThemeEngine | None = None


def get_theme_engine() -> ThemeEngine:
    global _engine
    if _engine is None:
        _engine = ThemeEngine()
    return _engine


def init_theme(app: QApplication):
    """Initialize theme engine and detect system theme."""
    engine = get_theme_engine()
    engine.mode = ThemeMode.AUTO
    QTimer.singleShot(0, engine.apply)
