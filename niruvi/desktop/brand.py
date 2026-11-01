"""Single source of truth for Niruvi's own branding assets.

The window icon, the About dialog, the installer, settings and the generated
desktop entry must all show the *same* logo. Previously each of those searched
for ``niruvi.png`` / ``niruvi.svg`` independently, which is how the About
dialog ended up with no artwork at all while the window had an icon.
"""

import os

# XDG-compliant icon name. The generated .desktop entry must reference this
# rather than an absolute path so it keeps working when Niruvi is moved.
BRAND_ICON_NAME = "io.github.putinservai_cyber.niruvi"

# Prefer SVG: it stays sharp at every size and avoids upscaling a small raster.
_BRAND_FILENAMES = ("niruvi.png",)


def _candidate_dirs() -> list[str]:
    dirs: list[str] = []
    appdir = os.environ.get("APPDIR")
    if appdir:
        dirs.append(appdir)

    icon_dir = os.environ.get("NIRUVI_ICON_DIR")
    if icon_dir:
        dirs.append(os.path.dirname(icon_dir))
        dirs.append(os.path.dirname(os.path.dirname(icon_dir)))

    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    dirs.append(project_root)
    dirs.append(os.path.join(project_root, "asset"))

    from niruvi.config import get_data_dir

    data_dir = get_data_dir()
    dirs.append(data_dir)
    dirs.append(os.path.join(data_dir, "assets"))

    seen: set[str] = set()
    unique: list[str] = []
    for d in dirs:
        if d and d not in seen:
            seen.add(d)
            unique.append(d)
    return unique


def brand_icon_path() -> str | None:
    """Absolute path to the Niruvi logo, or None when it cannot be found."""
    for directory in _candidate_dirs():
        for name in _BRAND_FILENAMES:
            candidate = os.path.join(directory, name)
            try:
                if os.path.isfile(candidate) and os.path.getsize(candidate) > 0:
                    return candidate
            except OSError:
                continue
    return None


def brand_icon(size: int | None = None):
    """QIcon for the Niruvi logo. Uses system theme first, falls back to file."""
    from PyQt6.QtGui import QIcon

    # First try system theme (will use our installed icons in asset/icons)
    icon = QIcon.fromTheme(BRAND_ICON_NAME)
    if not icon.isNull():
        return icon

    # Fall back to file
    path = brand_icon_path()
    if path:
        icon = QIcon(path)
        if not icon.isNull():
            return icon
    from niruvi.utils import get_icon

    return get_icon("package-x-generic", "applications-system", "dialog-information")


def install_brand_icon(size: int = 256) -> str | None:
    """Copy the logo into the user icon theme so desktop entries can find it.

    Returns the installed icon theme name on success, None otherwise. Safe to
    call repeatedly; existing correct installs are left alone.
    """
    source = brand_icon_path()
    if not source:
        return None

    xdg_data_home = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    icons_root = os.path.join(xdg_data_home, "icons", "hicolor")

    # Install all sizes from our asset/icons/hicolor
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    asset_hicolor = os.path.join(project_root, "asset", "icons", "hicolor")

    if os.path.isdir(asset_hicolor):
        import shutil

        try:
            for size_dir in os.listdir(asset_hicolor):
                src_dir = os.path.join(asset_hicolor, size_dir, "apps")
                if not os.path.isdir(src_dir):
                    continue
                dest_dir = os.path.join(icons_root, size_dir, "apps")
                os.makedirs(dest_dir, exist_ok=True)
                for fname in os.listdir(src_dir):
                    if fname.startswith("io.github.putinservai_cyber.niruvi"):
                        src = os.path.join(src_dir, fname)
                        dest = os.path.join(dest_dir, fname)
                        if not os.path.isfile(dest) or os.path.getsize(dest) != os.path.getsize(src):
                            shutil.copy2(src, dest)
        except OSError:
            pass

    # Also install symbolic icon for dark themes
    dark_symbolic_src = os.path.join(
        project_root, "asset", "icons", "dark", "symbolic", "apps", "io.github.putinservai_cyber.niruvi-symbolic.svg"
    )
    if os.path.isfile(dark_symbolic_src):
        try:
            xdg_data_home = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
            dest_dir = os.path.join(xdg_data_home, "icons", "hicolor", "symbolic", "apps")
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir, "io.github.putinservai_cyber.niruvi-symbolic.svg")
            if not os.path.isfile(dest) or os.path.getsize(dest) != os.path.getsize(dark_symbolic_src):
                shutil.copy2(dark_symbolic_src, dest)
        except OSError:
            pass

    return BRAND_ICON_NAME
