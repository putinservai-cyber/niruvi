"""Theme Engine — native KDE Breeze look via Fusion style + exact Breeze palette.

Strategy: Set Fusion style + exact Breeze QPalette colors. Fusion renders
everything natively from the palette. QSS is MINIMAL — only adds what
Fusion cannot do (selection alpha, hover states, thin scrollbar, tab underline).
"""

import logging
from collections.abc import Callable
from enum import Enum, auto

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

logger = logging.getLogger(__name__)


class ThemeMode(Enum):
    LIGHT = auto()
    DARK = auto()
    AUTO = auto()


# ── Semantic colors (always visible on both light and dark backgrounds) ──

COLOR_SUCCESS = "#27AE60"
COLOR_ERROR = "#DA4453"
COLOR_WARNING = "#F67400"
COLOR_INFO = "#2980B9"


def disabled_text_color() -> str:
    """Return the current palette's disabled text color as hex string."""
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        return "#707D8A"
    return app.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text).name()


# ── Breeze Light palette (exact values from BreezeLight.colors) ──────────


def _light_palette() -> QPalette:
    p = QPalette()
    # Window
    p.setColor(QPalette.ColorRole.Window, QColor(239, 240, 241))  # #EFF0F1
    p.setColor(QPalette.ColorRole.WindowText, QColor(35, 38, 41))  # #232629
    p.setColor(QPalette.ColorRole.Base, QColor(255, 255, 255))  # #FFFFFF
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(247, 247, 247))  # #F7F7F7
    p.setColor(QPalette.ColorRole.Text, QColor(35, 38, 41))  # #232629
    p.setColor(QPalette.ColorRole.Button, QColor(252, 252, 252))  # #FCFCFC
    p.setColor(QPalette.ColorRole.ButtonText, QColor(35, 38, 41))  # #232629
    p.setColor(QPalette.ColorRole.Highlight, QColor(61, 174, 233))  # #3DAEE9
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))  # #FFFFFF
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(247, 247, 247))  # #F7F7F7
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(35, 38, 41))  # #232629
    p.setColor(QPalette.ColorRole.Link, QColor(41, 128, 185))  # #2980B9
    p.setColor(QPalette.ColorRole.LinkVisited, QColor(155, 89, 182))  # #9B59B6
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(112, 125, 138))  # #707D8A
    p.setColor(QPalette.ColorRole.BrightText, QColor(218, 68, 83))  # #DA4453
    p.setColor(QPalette.ColorRole.Light, QColor(255, 255, 255))  # #FFFFFF
    p.setColor(QPalette.ColorRole.Midlight, QColor(247, 247, 247))  # #F7F7F7
    p.setColor(QPalette.ColorRole.Dark, QColor(209, 213, 219))  # #D1D5DB (computed)
    p.setColor(QPalette.ColorRole.Mid, QColor(227, 229, 231))  # #E3E5E7
    p.setColor(QPalette.ColorRole.Shadow, QColor(156, 163, 175))  # #9CA3AF
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(112, 125, 138))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(112, 125, 138))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(112, 125, 138))
    return p


# ── Breeze Dark palette (exact values from BreezeDark.colors) ────────────


def _dark_palette() -> QPalette:
    p = QPalette()
    # Window
    p.setColor(QPalette.ColorRole.Window, QColor(32, 35, 38))  # #202326
    p.setColor(QPalette.ColorRole.WindowText, QColor(252, 252, 252))  # #FCFCFC
    p.setColor(QPalette.ColorRole.Base, QColor(20, 22, 24))  # #141618
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(29, 31, 34))  # #1D1F22
    p.setColor(QPalette.ColorRole.Text, QColor(252, 252, 252))  # #FCFCFC
    p.setColor(QPalette.ColorRole.Button, QColor(41, 44, 48))  # #292C30
    p.setColor(QPalette.ColorRole.ButtonText, QColor(252, 252, 252))  # #FCFCFC
    p.setColor(QPalette.ColorRole.Highlight, QColor(61, 174, 233))  # #3DAEE9
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(252, 252, 252))  # #FCFCFC
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(41, 44, 48))  # #292C30
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(252, 252, 252))  # #FCFCFC
    p.setColor(QPalette.ColorRole.Link, QColor(29, 153, 243))  # #1D99F3
    p.setColor(QPalette.ColorRole.LinkVisited, QColor(155, 89, 182))  # #9B59B6
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(161, 169, 177))  # #A1A9B1
    p.setColor(QPalette.ColorRole.BrightText, QColor(248, 113, 113))  # #F87171
    p.setColor(QPalette.ColorRole.Light, QColor(75, 78, 85))  # #4B4E55 (computed)
    p.setColor(QPalette.ColorRole.Midlight, QColor(68, 71, 78))  # #44474E
    p.setColor(QPalette.ColorRole.Dark, QColor(55, 58, 64))  # #373A40
    p.setColor(QPalette.ColorRole.Mid, QColor(41, 44, 48))  # #292C30
    p.setColor(QPalette.ColorRole.Shadow, QColor(0, 0, 0))  # #000000
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(161, 169, 177))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(161, 169, 177))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(161, 169, 177))
    return p


# ── Minimal QSS — only what Fusion cannot do natively ────────────────────
# Fusion handles: buttons, combos, inputs, progress bars, checkboxes, radios,
# tree/list/table, tabs, menus, tooltips, splitters, groupboxes, sliders.
# We only add: selection alpha, hover states, scrollbar thinning, tab underline.

_QSS = """
/* ── Tree/List/Table: selection + hover ── */
QTreeView, QListView, QTableView {
    outline: none;
}
QTreeView::item, QListView::item, QTableView::item {
    padding: 2px 4px;
}
QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {
    background-color: rgba(61, 174, 233, 0.33);
    color: #FCFCFC;
}
QTreeView::item:!selected:hover, QListView::item:!selected:hover, QTableView::item:!selected:hover {
    background-color: rgba(61, 174, 233, 0.15);
}
QTreeView::item:!selected:hover:alternate, QListView::item:!selected:hover:alternate {
    background-color: rgba(61, 174, 233, 0.15);
}

/* ── Scrollbar: thin + minimal ── */
QScrollBar:vertical {
    width: 8px;
    background: transparent;
    margin: 0;
}
QScrollBar::handle:vertical {
    min-height: 20px;
    border-radius: 3px;
    background: rgba(0, 0, 0, 0.15);
}
QScrollBar::handle:vertical:hover {
    background: rgba(0, 0, 0, 0.25);
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
    background: transparent;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}
QScrollBar:horizontal {
    height: 8px;
    background: transparent;
    margin: 0;
}
QScrollBar::handle:horizontal {
    min-width: 20px;
    border-radius: 3px;
    background: rgba(0, 0, 0, 0.15);
}
QScrollBar::handle:horizontal:hover {
    background: rgba(0, 0, 0, 0.25);
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
    background: transparent;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: transparent;
}

/* ── Tab bar: underline indicator on selected tab ── */
QTabBar::tab {
    padding: 6px 12px;
    border: none;
    background: transparent;
}
QTabBar::tab:selected {
    border-bottom: 3px solid #3DAEE9;
}
QTabBar::tab:hover:!selected {
    background: rgba(61, 174, 233, 0.1);
}

/* ── Menu: no visible frame, minimal padding ── */
QMenu {
    padding: 4px 0;
}
QMenu::item {
    padding: 6px 24px 6px 12px;
}
QMenu::item:selected {
    background-color: #3DAEE9;
    color: #FFFFFF;
}
QMenu::separator {
    height: 1px;
    margin: 4px 8px;
}

/* ── Tooltip: Breeze style ── */
QToolTip {
    border: 1px solid rgba(35, 38, 41, 0.2);
    padding: 2px;
}

/* ── Focus ring ── */
QPushButton:focus, QComboBox:focus, QLineEdit:focus, QTextEdit:focus {
    border-color: #3DAEE9;
}
"""


class ThemeEngine:
    """Applies Breeze-accurate light/dark theme via Fusion style + palette.

    Users can override individual palette colors (a "custom theme") by
    supplying a dict of QPalette.ColorRole name -> "#RRGGBB" via
    set_custom_colors(). Overrides are layered on top of the Breeze
    light/dark base palette, so a partial override (e.g. just Highlight)
    is enough to accent the app without redefining every color.
    """

    def __init__(self):
        self._mode = ThemeMode.AUTO
        self._listeners: list[Callable[[ThemeMode], None]] = []
        self._custom_colors: dict[str, str] = {}
        self._theme_cache: ThemeMode | None = None
        self._theme_cache_time: float = 0.0

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

    def on_theme_changed(self, callback: Callable[[ThemeMode], None]):
        self._listeners.append(callback)

    def _notify(self):
        for cb in self._listeners:
            try:
                cb(self._mode)
            except Exception as e:
                logger.warning("Theme listener error: %s", e)

    def _detect_system_theme(self) -> ThemeMode:
        import time

        now = time.monotonic()
        if self._theme_cache is not None and now - self._theme_cache_time < 60:
            return self._theme_cache

        for probe in (
            self._probe_xdg_portal,
            self._probe_gsettings,
            self._probe_kreadconfig,
            self._probe_xfconf,
        ):
            mode = probe()
            if mode is not None:
                self._theme_cache = mode
                self._theme_cache_time = now
                return mode

        self._theme_cache = ThemeMode.LIGHT
        self._theme_cache_time = now
        return ThemeMode.LIGHT

    @staticmethod
    def _probe_xdg_portal() -> "ThemeMode | None":
        """Query org.freedesktop.appearance color-scheme via the XDG Desktop
        Portal. This is the desktop-agnostic standard (works under GNOME,
        KDE, XFCE-with-portal, Cinnamon, and any other DE that implements
        the portal spec) and should be tried before DE-specific tools.

        Return values per the spec: 0 = no preference, 1 = prefer dark,
        2 = prefer light.
        """
        import subprocess

        try:
            result = subprocess.run(
                [
                    "gdbus",
                    "call",
                    "--session",
                    "--dest",
                    "org.freedesktop.portal.Desktop",
                    "--object-path",
                    "/org/freedesktop/portal/desktop",
                    "--method",
                    "org.freedesktop.portal.Settings.Read",
                    "org.freedesktop.appearance",
                    "color-scheme",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return None
            out = result.stdout
            # Typical output: (<<uint32 1>>,)
            if "uint32 1" in out:
                return ThemeMode.DARK
            if "uint32 2" in out:
                return ThemeMode.LIGHT
            return None
        except Exception as e:
            logger.debug("XDG portal color-scheme detection failed: %s", e, exc_info=True)
            return None

    @staticmethod
    def _probe_gsettings() -> "ThemeMode | None":
        import subprocess

        try:
            result = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if "dark" in result.stdout.lower():
                return ThemeMode.DARK
            if result.returncode == 0 and result.stdout.strip():
                return ThemeMode.LIGHT
            return None
        except Exception as e:
            logger.debug("gsettings color-scheme detection failed: %s", e, exc_info=True)
            return None

    @staticmethod
    def _probe_kreadconfig() -> "ThemeMode | None":
        import subprocess

        try:
            result = subprocess.run(
                ["kreadconfig6", "--group", "General", "--key", "ColorScheme", "--default", "Breeze"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return None
            if "dark" in result.stdout.lower():
                return ThemeMode.DARK
            if result.stdout.strip():
                return ThemeMode.LIGHT
            return None
        except Exception as e:
            logger.debug("kreadconfig6 color-scheme detection failed: %s", e, exc_info=True)
            return None

    @staticmethod
    def _probe_xfconf() -> "ThemeMode | None":
        """XFCE stores the GTK theme name under xsettings; names containing
        'dark' are used by the common dark variants (e.g. Greybird-dark)."""
        import subprocess

        try:
            result = subprocess.run(
                ["xfconf-query", "-c", "xsettings", "-p", "/Net/ThemeName"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return None
            if "dark" in result.stdout.lower():
                return ThemeMode.DARK
            if result.stdout.strip():
                return ThemeMode.LIGHT
            return None
        except Exception as e:
            logger.debug("xfconf-query theme detection failed: %s", e, exc_info=True)
            return None

    def set_custom_colors(self, colors: dict[str, str]):
        """Set custom palette overrides, e.g. {"Highlight": "#7C3AED"}.

        Keys must match QPalette.ColorRole member names. Invalid keys or
        malformed hex values are skipped (logged) rather than raising, so a
        bad settings file can't crash startup. Call apply() afterwards, or
        just re-set `.mode` to trigger a re-apply.
        """
        validated: dict[str, str] = {}
        for role_name, hex_value in colors.items():
            if not hasattr(QPalette.ColorRole, role_name):
                logger.warning("Unknown palette role in custom theme: %s", role_name)
                continue
            if not QColor(hex_value).isValid():
                logger.warning("Invalid color for %s in custom theme: %s", role_name, hex_value)
                continue
            validated[role_name] = hex_value
        self._custom_colors = validated

    def clear_custom_colors(self):
        self._custom_colors = {}

    def apply(self):
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return
        # Set Fusion style first — it renders everything from the palette
        style = app.style()
        if style is not None and style.name() != "Fusion":
            app.setStyle("Fusion")
        # Set the exact Breeze palette — Fusion reads all colors from this
        mode = self.effective_mode
        palette = _dark_palette() if mode == ThemeMode.DARK else _light_palette()
        for role_name, hex_value in self._custom_colors.items():
            role = getattr(QPalette.ColorRole, role_name)
            palette.setColor(role, QColor(hex_value))
        app.setPalette(palette)
        # Minimal QSS — only things Fusion cannot do natively
        app.setStyleSheet(_QSS)


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
