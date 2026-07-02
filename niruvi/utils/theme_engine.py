"""Theme Engine — manages application appearance.

Supports light/dark/auto theme switching, QSS stylesheet management,
icon theme initialization, and accent color configuration.
"""

import logging
import os
from enum import Enum, auto

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QPalette, QColor
from PyQt6.QtWidgets import QApplication

logger = logging.getLogger(__name__)


class ThemeMode(Enum):
    LIGHT = auto()
    DARK = auto()
    AUTO = auto()


class ThemeEngine:
    """Central theme manager for Niruvi.

    Call `apply()` after changing settings to update the application
    appearance. Connect to `theme_changed` to react to theme switches.
    """

    def __init__(self):
        self._mode = ThemeMode.LIGHT
        self._accent_color = "#4a90d9"
        self._custom_qss: str = ""
        self._dark_qss: str = ""
        self._light_qss: str = ""
        self._listeners: list[callable] = []

    @property
    def mode(self) -> ThemeMode:
        return self._mode

    @mode.setter
    def mode(self, value: ThemeMode):
        self._mode = value
        self.apply()

    @property
    def accent_color(self) -> str:
        return self._accent_color

    @accent_color.setter
    def accent_color(self, value: str):
        self._accent_color = value
        self.apply()

    def set_dark_stylesheet(self, qss: str):
        self._dark_qss = qss

    def set_light_stylesheet(self, qss: str):
        self._light_qss = qss

    def set_custom_stylesheet(self, qss: str):
        self._custom_qss = qss

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
                capture_output=True, text=True, timeout=5,
            )
            if "dark" in result.stdout.lower():
                return ThemeMode.DARK
        except Exception:
            pass
        try:
            result = subprocess.run(
                ["kreadconfig6", "--group", "General", "--key", "ColorScheme",
                 "--default", "Breeze"],
                capture_output=True, text=True, timeout=5,
            )
            if "dark" in result.stdout.lower():
                return ThemeMode.DARK
        except Exception:
            pass
        return ThemeMode.LIGHT

    def _effective_mode(self) -> ThemeMode:
        if self._mode == ThemeMode.AUTO:
            return self._detect_system_theme()
        return self._mode

    def _build_stylesheet(self, mode: ThemeMode) -> str:
        base = ""
        if mode == ThemeMode.DARK:
            base = self._dark_qss
            if not base:
                base = (
                    "QWidget { background-color: #2b2b2b; color: #e0e0e0; }\n"
                    "QMenuBar { background: #333; color: #e0e0e0; }\n"
                    "QMenuBar::item:selected { background: #4a90d9; }\n"
                    "QMenu { background: #333; color: #e0e0e0; border: 1px solid #555; }\n"
                    "QPushButton { background: #444; color: #e0e0e0; border: 1px solid #666; "
                    "border-radius: 4px; padding: 4px 12px; }\n"
                    "QPushButton:hover { background: #555; }\n"
                    "QLineEdit { background: #3a3a3a; color: #e0e0e0; "
                    "border: 1px solid #555; border-radius: 4px; padding: 4px; }\n"
                    "QListWidget { background: #333; color: #e0e0e0; "
                    "border: 1px solid #555; border-radius: 4px; }\n"
                    "QListWidget::item:selected { background: #4a90d9; }\n"
                    "QProgressBar { background: #444; border: 1px solid #555; "
                    "border-radius: 4px; text-align: center; }\n"
                    "QProgressBar::chunk { background: #4a90d9; border-radius: 3px; }\n"
                    "QGroupBox { border: 1px solid #555; border-radius: 6px; "
                    "margin-top: 8px; padding-top: 14px; }\n"
                    "QGroupBox::title { color: #4a90d9; }\n"
                    "QScrollBar:vertical { background: #333; width: 10px; }\n"
                    "QScrollBar::handle:vertical { background: #555; border-radius: 5px; }\n"
                )
        else:
            base = self._light_qss
            if not base:
                base = (
                    "QGroupBox { border: 1px solid palette(mid); border-radius: 6px; "
                    "margin-top: 8px; padding-top: 14px; }\n"
                )
        if self._custom_qss:
            base += "\n" + self._custom_qss
        return base

    def apply(self):
        app = QApplication.instance()
        if app is None:
            return
        mode = self._effective_mode()
        stylesheet = self._build_stylesheet(mode)
        if stylesheet:
            app.setStyleSheet(stylesheet)
        palette = self._palette_for_mode(mode)
        if palette:
            app.setPalette(palette)
        self._notify()

    def _palette_for_mode(self, mode: ThemeMode) -> QPalette | None:
        if mode == ThemeMode.DARK:
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
            return p
        return None


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
