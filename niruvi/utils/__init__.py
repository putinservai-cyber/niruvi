import os
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import QApplication

from niruvi.utils.icons import refresh_icons

# Standard freedesktop icon name aliases for common Niruvi icons
_FREEDESKTOP_ALIASES: dict[str, tuple[str, ...]] = {
    "package-x-generic": ("application-x-archive", "application-x-executable"),
    "list-add": ("document-new", "list-add"),
    "document-open": ("document-open",),
    "document-save": ("document-save",),
    "document-save-as": ("document-save-as",),
    "edit-delete": ("edit-delete", "user-trash"),
    "view-refresh": ("view-refresh",),
    "application-exit": ("application-exit", "system-log-out"),
    "preferences-system": ("preferences-system",),
    "applications-internet": ("applications-internet", "folder-download", "emblem-downloads"),
    "emblem-system": ("emblem-system", "applications-utilities", "document-export"),
    "computer": ("computer", "video-display", "system-search"),
    "drive-harddisk": ("drive-harddisk", "folder"),
    "security-high": ("security-high", "preferences-system"),
    "emblem-downloads": ("emblem-downloads", "system-software-update", "download", "document-save"),
    "network-server": ("network-server", "emblem-downloads"),
    "help-contents": ("help-contents",),
    "bug": ("bug", "tools-report-bug", "dialog-warning"),
    "emblem-documents": ("emblem-documents", "help-about", "document-properties"),
    "help-about": ("help-about",),
    "emblem-favorite": ("emblem-favorite", "favorites", "starred"),
    "folder-open": ("folder-open",),
    "document-revert": ("document-revert", "edit-undo"),
    "edit-copy": ("edit-copy",),
    "go-previous": ("go-previous",),
    "go-next": ("go-next",),
    "dialog-ok": ("dialog-ok", "dialog-ok-apply"),
    "dialog-cancel": ("dialog-cancel",),
    "dialog-warning": ("dialog-warning",),
    "dialog-error": ("dialog-error",),
    "dialog-information": ("dialog-information", "dialog-info"),
    "dialog-close": ("dialog-close", "window-close"),
    "edit-undo": ("edit-undo", "document-revert"),
    "format-justify-left": ("format-justify-left",),
    "media-playback-start": ("media-playback-start",),
    "process-stop": ("process-stop",),
    "window-close": ("window-close", "dialog-close"),
    "system-software-update": ("system-software-update", "view-refresh"),
    "emblem-ok": ("emblem-ok", "dialog-ok", "dialog-ok-apply"),
    "emblem-warning": ("emblem-warning", "dialog-warning"),
    "edit-find": ("edit-find", "system-search"),
    "document-new": ("document-new", "list-add"),
    "edit-copy-symbolic": ("edit-copy",),
    "go-next-symbolic": ("go-next",),
    "emblem-favorite-symbolic": ("emblem-favorite",),
    "clipboard-copy": ("clipboard-copy", "edit-copy"),
    "dialog-info": ("dialog-information",),
    "view-restore": ("view-restore", "go-home"),
    "emblem-photos": ("emblem-photos", "image-x-generic"),
    "image-x-generic": ("image-x-generic",),
    "tools-report-bug": ("tools-report-bug", "bug", "dialog-warning"),
    "document-properties": ("document-properties",),
    "folder-download": ("folder-download", "applications-internet", "emblem-downloads"),
    "applications-utilities": ("applications-utilities", "emblem-system"),
    "hard-drive": ("drive-harddisk", "hard-drive"),
    "download": ("folder-download", "download"),
    "applications-system": ("applications-system",),
    "text-x-script": ("text-x-script", "format-justify-left", "document-properties"),
    "emblem-default": ("emblem-default", "dialog-ok-apply", "emblem-ok"),
    "document-save-as": ("document-save-as", "document-save"),
    "list-remove": ("list-remove", "edit-delete"),
    "video-display": ("video-display", "computer"),
    "go-home": ("go-home", "view-restore"),
}


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
