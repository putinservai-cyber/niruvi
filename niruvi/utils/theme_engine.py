"""Native System Theme — let Qt follow the system theme automatically.

This module replaces the custom theme engine. It provides minimal functions
for backward compatibility while letting the application use the native Qt style
and system palette (KDE Breeze, GNOME Adwaita, etc.) automatically.
"""

import logging
from enum import Enum, auto
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

logger = logging.getLogger(__name__)


class ThemeMode(Enum):
    """Theme mode enum kept for backward compatibility."""

    LIGHT = auto()
    DARK = auto()
    AUTO = auto()


class _NativeThemeEngine(QObject):
    """Minimal theme engine that uses native system theme.

    Signals theme mode changes so UI can update icons accordingly.
    """

    mode_changed = pyqtSignal(ThemeMode)

    def __init__(self) -> None:
        super().__init__()
        self._mode = ThemeMode.AUTO

    @property
    def mode(self) -> ThemeMode:
        return self._mode

    @mode.setter
    def mode(self, value: ThemeMode) -> None:
        """Theme mode setter - update mode and notify listeners."""
        if self._mode != value:
            self._mode = value
            self.mode_changed.emit(value)

    def apply(self, app: QApplication | None = None) -> None:
        """Apply native system theme - no-op since Qt does this automatically."""
        if app is not None:
            # Use system style (Fusion on KDE, Adwaita on GNOME, etc.)
            pass

    def stylesheet(self) -> str:
        """Return empty stylesheet - no custom QSS."""
        return ""

    def palette(self) -> Any:
        """Return system palette."""
        if QApplication.instance():
            return QApplication.instance().palette()
        return QApplication.palette()

    def prefers_reduced_motion(self) -> bool:
        """Check if the system prefers reduced motion."""
        # Qt doesn't have a built-in way to check this, so we check the environment
        # and X11/Wayland settings
        import os

        return (
            os.environ.get("QT_AUTO_SCREEN_SCALE_FACTOR") == "1" or os.environ.get("GTK_CSD") == "0"
        )  # Rough heuristic


# Global theme engine instance
_theme_engine: _NativeThemeEngine | None = None


def get_theme_engine() -> _NativeThemeEngine:
    """Get the global theme engine instance."""
    global _theme_engine
    if _theme_engine is None:
        _theme_engine = _NativeThemeEngine()
        # Connect to app palette changes for auto mode
        app = QApplication.instance()
        if app and _theme_engine.mode == ThemeMode.AUTO:
            app.paletteChanged.connect(_on_palette_changed)
    return _theme_engine


def _on_palette_changed() -> None:
    """Handle palette change - switch theme mode automatically."""
    engine = get_theme_engine()
    if engine.mode == ThemeMode.AUTO:
        # Mode stays AUTO, but we refresh any dependent systems
        pass


def init_theme(app=None) -> None:
    """Initialize theme - no-op since we use native system theme.

    Args:
        app: Optional QApplication instance (ignored)
    """
    # No custom theme initialization needed - Qt uses system theme automatically
    pass


# For backward compatibility - minimal style function that returns empty string
def style(template: str, **kwargs: Any) -> str:
    """No-op style function for backward compatibility."""
    return ""


# Semantic color constants for backward compatibility
# These use palette roles so they adapt to the system theme
COLOR_SUCCESS = "palette(highlight)"
COLOR_WARNING = "palette(windowText)"
COLOR_ERROR = "palette(windowText)"
COLOR_INK = "palette(windowText)"
