"""Utility functions and re-exports for Niruvi."""

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QIcon, QPixmap

from niruvi.utils.icon_aliases import _FREEDESKTOP_ALIASES
from niruvi.utils.icons import refresh_icons

__all__ = ["get_icon", "icon_pixmap", "refresh_icons"]


def get_icon(*names: str) -> QIcon:
    """Resolve a system icon using standard Freedesktop icon names.

    Prioritizes QIcon.fromTheme() with standard freedesktop names.
    Falls back through aliases if an icon is missing from the current theme.
    """
    # Try each name and its aliases through system theme
    for name in names:
        # Try the name directly first
        icon = QIcon.fromTheme(name)
        if not icon.isNull():
            return icon

        # Try standard freedesktop fallbacks
        for fallback in _FREEDESKTOP_ALIASES.get(name, ()):
            icon = QIcon.fromTheme(fallback)
            if not icon.isNull():
                return icon

    # Last resort: generic system icons
    for generic in ("package-x-generic", "application-x-executable", "dialog-information"):
        icon = QIcon.fromTheme(generic)
        if not icon.isNull():
            return icon

    # Ultimate fallback: empty icon (will be invisible but not crash)
    return QIcon()


def icon_pixmap(icon: QIcon, size: int, dpr: float | None = None) -> QPixmap:
    """Render `icon` at `size` logical px, crisp on HiDPI displays.

    ``QIcon.pixmap(w, h)`` ignores the device pixel ratio, so icons look
    blurry at 1.25x/1.5x/2x scaling. This renders at ``size * dpr`` physical
    pixels and tags the result, so Qt downsamples it for display.
    """
    if icon is None or icon.isNull():
        return QPixmap()
    if dpr is None:
        from niruvi.desktop.icon_utils import screen_dpr

        dpr = screen_dpr()
    if dpr <= 0:
        dpr = 1.0
    return icon.pixmap(QSize(size, size), dpr)
