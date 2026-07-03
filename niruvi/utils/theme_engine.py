"""Theme Engine — manages light/dark appearance using Fusion style + QPalette.

Uses Qt's Fusion style as the base, which properly respects QPalette for
consistent cross-platform appearance. Detects the system theme for auto mode.
"""

import logging
from enum import Enum, auto

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

logger = logging.getLogger(__name__)


class ThemeMode(Enum):
    LIGHT = auto()
    DARK = auto()
    AUTO = auto()


def _light_palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor(240, 240, 240))
    p.setColor(QPalette.ColorRole.WindowText, QColor(30, 30, 30))
    p.setColor(QPalette.ColorRole.Base, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(245, 245, 245))
    p.setColor(QPalette.ColorRole.Text, QColor(30, 30, 30))
    p.setColor(QPalette.ColorRole.Button, QColor(240, 240, 240))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(30, 30, 30))
    p.setColor(QPalette.ColorRole.Highlight, QColor(74, 144, 217))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(30, 30, 30))
    p.setColor(QPalette.ColorRole.Link, QColor(74, 144, 217))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(160, 160, 160))
    p.setColor(QPalette.ColorRole.BrightText, QColor(34, 139, 34))
    return p


def _dark_palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor(43, 43, 43))
    p.setColor(QPalette.ColorRole.WindowText, QColor(224, 224, 224))
    p.setColor(QPalette.ColorRole.Base, QColor(51, 51, 51))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(58, 58, 58))
    p.setColor(QPalette.ColorRole.Text, QColor(224, 224, 224))
    p.setColor(QPalette.ColorRole.Button, QColor(68, 68, 68))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(224, 224, 224))
    p.setColor(QPalette.ColorRole.Highlight, QColor(74, 144, 217))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(51, 51, 51))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(224, 224, 224))
    p.setColor(QPalette.ColorRole.Link, QColor(74, 144, 217))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(120, 120, 120))
    p.setColor(QPalette.ColorRole.BrightText, QColor(255, 80, 80))
    return p


class ThemeEngine:
    """Applies light/dark Fusion theme with system detection for auto mode."""

    def __init__(self):
        self._mode = ThemeMode.AUTO
        self._listeners: list[callable] = []

    @property
    def mode(self) -> ThemeMode:
        return self._mode

    @mode.setter
    def mode(self, value: ThemeMode):
        self._mode = value
        self.apply()
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
        app = QApplication.instance()
        if app is None:
            return
        app.setStyle("Fusion")
        mode = self.effective_mode
        palette = _dark_palette() if mode == ThemeMode.DARK else _light_palette()
        app.setPalette(palette)


_engine: ThemeEngine | None = None


def get_theme_engine() -> ThemeEngine:
    global _engine
    if _engine is None:
        _engine = ThemeEngine()
    return _engine


def init_theme(app: QApplication):
    """Initialize theme engine and apply system-aware theme."""
    engine = get_theme_engine()
    engine.mode = ThemeMode.AUTO
    QTimer.singleShot(0, engine.apply)
