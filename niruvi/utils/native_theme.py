"""Native Qt desktop integration utilities for Niruvi.

Provides palette colors, standard freedesktop icon lookups, and styling
helpers that let Niruvi follow the user's system theme and desktop conventions.
"""

from PyQt6.QtGui import QColor, QIcon, QPalette
from PyQt6.QtWidgets import QApplication

# Standard spacing and font tokens conforming to KDE/GTK HIG
SPACING_XS = 2
SPACING_S = 4
SPACING_M = 8
SPACING_L = 12
SPACING_XL = 16

RADIUS_S = 4
RADIUS_M = 8
RADIUS_L = 12

FONT_XS = 10
FONT_SM = 12
FONT_MD = 14
FONT_LG = 16
FONT_XL = 20


def setup_native_theme(app: QApplication) -> None:
    """Configure Qt application to follow native desktop theme and palette.

    Leaves Qt to use the desktop's native style (Breeze on KDE, Adwaita/Fusion on GNOME).
    Does not apply custom monolithic QSS stylesheets.
    """
    # Ensure system icon theme search paths are standard
    pass


def get_accent_color() -> QColor:
    """Get the desktop accent/highlight color from system QPalette."""
    app = QApplication.instance()
    if app:
        return app.palette().color(QPalette.ColorRole.Highlight)
    return QColor("#3daee9")


def get_text_color() -> QColor:
    """Get primary text color from system QPalette."""
    app = QApplication.instance()
    if app:
        return app.palette().color(QPalette.ColorRole.WindowText)
    return QColor("#232629")


def get_muted_text_color() -> QColor:
    """Get placeholder / muted text color from system QPalette."""
    app = QApplication.instance()
    if app:
        return app.palette().color(QPalette.ColorRole.PlaceholderText)
    return QColor("#7f8c8d")


def get_subtle_text_color() -> QColor:
    """Alias for muted text color."""
    return get_muted_text_color()


def get_background_color() -> QColor:
    """Get window background color from system QPalette."""
    app = QApplication.instance()
    if app:
        return app.palette().color(QPalette.ColorRole.Window)
    return QColor("#eff0f1")


def get_base_color() -> QColor:
    """Get base/input background color from system QPalette."""
    app = QApplication.instance()
    if app:
        return app.palette().color(QPalette.ColorRole.Base)
    return QColor("#ffffff")


def get_card_background_color() -> QColor:
    """Get card/alternate background color from system QPalette."""
    app = QApplication.instance()
    if app:
        return app.palette().color(QPalette.ColorRole.AlternateBase)
    return QColor("#f7f7f7")


def get_border_color() -> QColor:
    """Get border color from system QPalette (Mid / Dark)."""
    app = QApplication.instance()
    if app:
        return app.palette().color(QPalette.ColorRole.Mid)
    return QColor("#bdc3c7")


def get_error_color() -> QColor:
    """Get semantic error / danger color adapted to theme lightness."""
    app = QApplication.instance()
    if app and app.palette().color(QPalette.ColorRole.Window).lightness() < 128:
        return QColor("#e74c3c")
    return QColor("#c0392b")


def get_warning_color() -> QColor:
    """Get semantic warning color adapted to theme lightness."""
    app = QApplication.instance()
    if app and app.palette().color(QPalette.ColorRole.Window).lightness() < 128:
        return QColor("#f39c12")
    return QColor("#d35400")


def get_success_color() -> QColor:
    """Get semantic success color adapted to theme lightness."""
    app = QApplication.instance()
    if app and app.palette().color(QPalette.ColorRole.Window).lightness() < 128:
        return QColor("#2ecc71")
    return QColor("#27ae60")


def get_subtle_color() -> QColor:
    return get_muted_text_color()


def color_to_hex(color: QColor) -> str:
    """Format a QColor into #rrggbb format."""
    return color.name(QColor.NameFormat.HexRgb)


# Standard Freedesktop icon mappings (extended for Niruvi)
_FREEDESKTOP_FALLBACKS: dict[str, tuple[str, ...]] = {
    "bug": ("tools-report-bug", "dialog-warning"),
    "check": ("emblem-ok", "dialog-ok", "dialog-ok-apply"),
    "database": ("drive-harddisk",),
    "hard-drive": ("drive-harddisk", "hard-drive"),
    "identification-card": ("dialog-information", "user-identity"),
    "scroll": ("text-x-script", "format-justify-left", "document-properties"),
    "arrow-right": ("go-next",),
    "clock": ("preferences-system",),
    "code": ("text-x-script",),
    "cpu": ("preferences-system",),
    "device-laptop": ("computer",),
    "monitor": ("video-display",),
    "starred": ("emblem-favorite",),
    "favorites": ("emblem-favorite",),
    "archive": ("application-x-archive",),
    "download": ("folder-download", "download"),
    "dialog-info": ("dialog-information", "dialog-info"),
    "clipboard-copy": ("edit-copy", "clipboard-copy"),
    "emblem-favorite-symbolic": ("emblem-favorite",),
    "go-next-symbolic": ("go-next",),
    "edit-copy-symbolic": ("edit-copy",),
    "list-add": ("document-new", "list-add"),
    "document-open": ("document-open",),
    "document-save": ("document-save",),
    "document-save-as": ("document-save-as", "document-save"),
    "edit-delete": ("edit-delete", "user-trash"),
    "view-refresh": ("view-refresh",),
    "application-exit": ("application-exit", "system-log-out"),
    "preferences-system": ("preferences-system",),
    "applications-internet": ("applications-internet", "folder-download", "emblem-downloads"),
    "emblem-system": ("emblem-system", "applications-utilities", "document-export"),
    "computer": ("computer", "video-display", "system-search"),
    "drive-harddisk": ("drive-harddisk",),
    "security-high": ("security-high", "preferences-system"),
    "emblem-downloads": ("emblem-downloads", "system-software-update", "download", "document-save"),
    "network-server": ("network-server", "emblem-downloads"),
    "help-contents": ("help-contents",),
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
    "go-home": ("go-home", "view-restore"),
}


def get_system_icon(*names: str) -> QIcon:
    """Resolve a system icon using standard Freedesktop icon names.

    Prioritizes QIcon.fromTheme() and tries fallbacks if an icon is missing.
    """
    for name in names:
        ic = QIcon.fromTheme(name)
        if not ic.isNull():
            return ic
        for fallback in _FREEDESKTOP_FALLBACKS.get(name, ()):
            ic = QIcon.fromTheme(fallback)
            if not ic.isNull():
                return ic

    # Last resort fallback to standard generic icons
    for generic in ("package-x-generic", "application-x-executable", "dialog-information"):
        ic = QIcon.fromTheme(generic)
        if not ic.isNull():
            return ic

    return QIcon()
