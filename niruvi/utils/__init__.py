import os
import shutil
import tempfile

from PyQt6.QtCore import QDir
from PyQt6.QtGui import QColor, QIcon, QPalette, QPixmap, QPixmapCache
from PyQt6.QtWidgets import QApplication

_svg_cache: dict[str, str] = {}
_known_icon_names: set[str] = set()
_colorized_dir: str | None = None
_icon_theme_initialized = False


def _init_icon_theme():
    global _icon_theme_initialized
    if _icon_theme_initialized:
        return

    search_dirs = []
    env_dir = os.environ.get("NIRUVI_ICON_DIR")
    if env_dir:
        search_dirs.append(env_dir)

    appdir = os.environ.get("APPDIR")
    if appdir:
        search_dirs.append(os.path.join(appdir, "icons"))

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for parent in (here, os.path.join(here, "niruvi"),
                   os.path.join(here, "asset"),
                   os.path.join(os.path.dirname(here), "asset")):
        search_dirs.append(os.path.join(parent, "icons"))

    icon_dir = None
    for d in search_dirs:
        if d and os.path.isdir(d):
            icon_dir = d
            break

    if icon_dir:
        paths = list(QIcon.themeSearchPaths())
        icon_dir = QDir(icon_dir).absolutePath()
        if icon_dir not in paths:
            paths.insert(0, icon_dir)
            QIcon.setThemeSearchPaths(paths)

    _icon_theme_initialized = True


def _load_svg(name: str) -> str | None:
    if name in _svg_cache:
        return _svg_cache[name]
    for path in QIcon.themeSearchPaths():
        svg_path = os.path.join(path, "Phosphor", "scalable", "actions", f"{name}.svg")
        if os.path.exists(svg_path):
            try:
                with open(svg_path) as f:
                    _svg_cache[name] = f.read()
                    return _svg_cache[name]
            except OSError:
                pass
    return None


def _get_colorized_dir() -> str:
    global _colorized_dir
    if _colorized_dir is None:
        _colorized_dir = os.path.join(tempfile.gettempdir(), "niruvi_phosphor")
        os.makedirs(_colorized_dir, exist_ok=True)
    return _colorized_dir


def _current_color_hex() -> str:
    app = QApplication.instance()
    if app:
        color = app.palette().color(QPalette.ColorRole.WindowText)
    else:
        color = QColor(0, 0, 0)
    return f"#{color.red():02x}{color.green():02x}{color.blue():02x}"


def _colorized_icon_path(name: str, svg: str) -> str:
    dest = os.path.join(_get_colorized_dir(), f"{name}.svg")
    colored = svg.replace("currentColor", _current_color_hex())
    with open(dest, "w") as f:
        f.write(colored)
    _known_icon_names.add(name)
    return dest


def _rebuild_icons():
    d = _get_colorized_dir()
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    color = _current_color_hex()
    for name in list(_known_icon_names):
        svg = _load_svg(name)
        if svg:
            fpath = os.path.join(d, f"{name}.svg")
            colored = svg.replace("currentColor", color)
            try:
                with open(fpath, "w") as f:
                    f.write(colored)
            except OSError:
                pass
    QPixmapCache.clear()


def _on_palette_changed(_palette=None):
    _rebuild_icons()


def _fallback_icon() -> QIcon:
    pixmap = QPixmapCache.find("niruvi_fallback_icon")
    if pixmap and not pixmap.isNull():
        return QIcon(pixmap)
    pixmap = QPixmap(32, 32)
    pixmap.fill(QColor(0x88, 0x88, 0x88))
    QPixmapCache.insert("niruvi_fallback_icon", pixmap)
    return QIcon(pixmap)


def get_icon(*names: str) -> QIcon:
    _init_icon_theme()
    app = QApplication.instance()
    if app and not hasattr(QIcon, "_niruvi_palette_connected"):
        app.paletteChanged.connect(_on_palette_changed)
        setattr(QIcon, "_niruvi_palette_connected", True)

    for name in names:
        svg = _load_svg(name)
        if svg:
            path = _colorized_icon_path(name, svg)
            icon = QIcon(path)
            if not icon.isNull():
                return icon

    for name in names:
        icon = QIcon.fromTheme(name)
        if not icon.isNull():
            return icon

    return QIcon.fromTheme("dialog-information", _fallback_icon())
