"""Centralized icon management with theme-aware Fluent UI System Icons."""

from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from niruvi.utils.icon_aliases import _FREEDESKTOP_ALIASES


# Get the asset icons directory
def _get_asset_icons_dir() -> Path:
    """Get the absolute path to the asset/icons/fluent directory."""
    current = Path(__file__).resolve()
    project_root = current.parent.parent.parent
    return project_root / "asset" / "icons" / "fluent"


_ASSET_ICONS_DIR: Path | None = None
_CURRENT_THEME: str = "auto"  # "auto", "light", "dark"
_ICON_CACHE: dict[str, QIcon] = {}


def _init_asset_icons_dir() -> None:
    """Initialize the asset icons directory path."""
    global _ASSET_ICONS_DIR
    if _ASSET_ICONS_DIR is None:
        _ASSET_ICONS_DIR = _get_asset_icons_dir()


def _resolve_theme() -> str:
    """Resolve the current theme to 'light' or 'dark'."""
    global _CURRENT_THEME

    if _CURRENT_THEME == "auto":
        app = QApplication.instance()
        if app:
            palette = app.palette()
            window_color = palette.color(palette.ColorRole.Window)
            if window_color.lightness() > 128:
                return "light"
            else:
                return "dark"
        return "light"
    return _CURRENT_THEME


def _get_theme_icon_dir() -> Path:
    """Get the icon directory for the current theme (light/dark)."""
    _init_asset_icons_dir()
    theme = _resolve_theme()
    return _ASSET_ICONS_DIR / theme


def _load_icon_direct(icon_name: str) -> QIcon | None:
    """Load an icon directly from the current theme directory.

    Fluent icons are organized flat in light/ and dark/ directories.
    """
    theme_dir = _get_theme_icon_dir()

    # Try different naming conventions
    possible_names = [
        f"{icon_name}.svg",
        f"{icon_name}-symbolic.svg",
    ]

    # Fluent icons are flat in light/ and dark/ directories
    for name in possible_names:
        icon_path = theme_dir / name
        if icon_path.exists():
            icon = QIcon(str(icon_path))
            if not icon.isNull():
                return icon

    return None


def _icon_from_theme_with_fallbacks(icon_name: str) -> QIcon:
    """Resolve an icon using QIcon.fromTheme with progressively broader fallbacks.

    Strategy:
    1. QIcon.fromTheme() with the requested name
    2. QIcon.fromTheme() with standard freedesktop aliases
    3. QIcon.fromTheme() with generic fallbacks
    4. Empty QIcon (ultimate fallback)
    """
    # Try the name directly first
    icon = QIcon.fromTheme(icon_name)
    if not icon.isNull():
        return icon

    # Try standard freedesktop fallbacks
    for fallback in _FREEDESKTOP_ALIASES.get(icon_name, ()):
        icon = QIcon.fromTheme(fallback)
        if not icon.isNull():
            return icon

    # Try generic system icons
    for generic in ("package-x-generic", "application-x-executable", "dialog-information"):
        icon = QIcon.fromTheme(generic)
        if not icon.isNull():
            return icon

    # Ultimate fallback: empty icon
    return QIcon()


def _load_icon_with_fallbacks(icon_name: str) -> QIcon:
    """Load an icon trying Flute theme first, then system fallbacks.

    Args:
        icon_name: The icon name to load

    Returns:
        QIcon object (never null, will be empty if nothing found)
    """
    # Try loading directly from our theme directory (Fluent theme)
    icon = _load_icon_direct(icon_name)
    if icon is not None and not icon.isNull():
        return icon

    # Fallback to system theme with aliases
    return _icon_from_theme_with_fallbacks(icon_name)


def get_folder_icon(name: str = "folder", size: int = 16) -> QIcon:
    """Get a folder icon for the current theme.

    Args:
        name: Icon name (e.g., 'folder', 'folder-open', 'folder-new')
        size: Desired icon size (used for rendering)

    Returns:
        QIcon for the folder, never null
    """
    cache_key = f"{name}:{size}:{_resolve_theme()}"

    if _ICON_CACHE.get(cache_key):
        return _ICON_CACHE[cache_key][0] if isinstance(_ICON_CACHE[cache_key], dict) else _ICON_CACHE[cache_key]

    _init_asset_icons_dir()

    # Try loading directly from our theme directory (Fluent theme)
    icon = _load_icon_direct(name)

    if icon is None or icon.isNull():
        # Fallback to system theme with aliases
        icon = _icon_from_theme_with_fallbacks(name)

    if icon is None or icon.isNull():
        # Try some common fallbacks
        for fallback in ("folder", "folder-open", "folder-new", "document-open"):
            if fallback != name:
                icon = QIcon.fromTheme(fallback)
                if not icon.isNull():
                    break

    if icon is None or icon.isNull():
        # Ultimate fallback
        icon = QIcon()

    # Cache the result
    if cache_key not in _ICON_CACHE:
        _ICON_CACHE[cache_key] = icon
    elif isinstance(_ICON_CACHE[cache_key], dict):
        _ICON_CACHE[cache_key][size] = icon

    return icon


def get_folder_open_icon(size: int = 16) -> QIcon:
    """Get the folder-open icon for the current theme."""
    return get_folder_icon("folder-open", size)


def get_folder_new_icon(size: int = 16) -> QIcon:
    """Get the folder-new icon for the current theme."""
    return get_folder_icon("folder-new", size)


def set_theme(theme: str) -> None:
    """Set the application theme and refresh icons.

    Args:
        theme: "auto", "light", or "dark"
    """
    global _CURRENT_THEME, _ICON_CACHE
    if theme not in ("auto", "light", "dark"):
        raise ValueError(f"Invalid theme: {theme}. Must be 'auto', 'light', or 'dark'")

    _CURRENT_THEME = theme
    _ICON_CACHE.clear()


def get_current_theme() -> str:
    """Get the current theme setting ('auto', 'light', 'dark')."""
    return _CURRENT_THEME


def refresh_icons() -> None:
    """Refresh all cached icons for the current theme."""
    global _ICON_CACHE
    _ICON_CACHE.clear()


# Initialize on import
_init_asset_icons_dir()
