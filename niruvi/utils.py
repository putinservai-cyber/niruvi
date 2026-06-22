import os
import shutil
import tempfile

from PyQt6.QtCore import QDir
from PyQt6.QtGui import QColor, QIcon, QPalette
from PyQt6.QtWidgets import QApplication

_colorized_dir: str | None = None


def _init_icon_theme():
    icon_dir = os.environ.get("NIRUVI_ICON_DIR")
    if not icon_dir:
        appdir = os.environ.get("APPDIR")
        if appdir:
            candidate = os.path.join(appdir, "icons")
            if os.path.isdir(candidate):
                icon_dir = candidate
    if not icon_dir:
        here = os.path.dirname(os.path.abspath(__file__))
        for parent in (here, os.path.dirname(here),
                       os.path.join(os.path.dirname(here), "asset"),
                       os.path.join(os.path.dirname(os.path.dirname(here)), "asset")):
            candidate = os.path.join(parent, "icons")
            if os.path.isdir(candidate):
                icon_dir = candidate
                break
    if icon_dir and os.path.isdir(icon_dir):
        paths = QIcon.themeSearchPaths()
        icon_dir = QDir(icon_dir).absolutePath()
        if icon_dir not in paths:
            paths.insert(0, icon_dir)
            QIcon.setThemeSearchPaths(paths)
        QIcon.setThemeName("Phosphor")


_init_icon_theme()

_svg_cache: dict[str, str] = {}


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
        _colorized_dir = tempfile.mkdtemp(prefix="niruvi_phosphor_")
    return _colorized_dir


def _invalidate_colorized():
    global _colorized_dir
    if _colorized_dir and os.path.isdir(_colorized_dir):
        shutil.rmtree(_colorized_dir, ignore_errors=True)
    _colorized_dir = None


def _on_palette_changed(_palette=None):
    _invalidate_colorized()


def _current_color() -> str:
    app = QApplication.instance()
    if app:
        color = app.palette().color(QPalette.ColorRole.WindowText)
    else:
        color = QColor(0, 0, 0)
    return f"#{color.red():02x}{color.green():02x}{color.blue():02x}"


def _colorized_icon_path(name: str, svg: str) -> str:
    dest = os.path.join(_get_colorized_dir(), f"{name}.svg")
    colored = svg.replace("currentColor", _current_color())
    with open(dest, "w") as f:
        f.write(colored)
    return dest


def get_icon(*names: str) -> QIcon:
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

    return QIcon.fromTheme("dialog-information")
