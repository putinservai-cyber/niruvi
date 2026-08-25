"""Theme Engine — native KDE Breeze look via Fusion style + exact Breeze palette.

Strategy: Set Fusion style + exact Breeze QPalette colors. Fusion renders
everything natively from the palette. QSS is MINIMAL — only adds what
Fusion cannot do (selection alpha, hover states, thin scrollbar, tab underline).
"""

import logging
import re
from collections.abc import Callable
from enum import Enum, auto

from PyQt6.QtGui import QColor, QFont, QPalette
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

# DESIGN.md semantic colors (light theme — the DESIGN.md reference palette)
COLOR_INK = "#1E293B"
COLOR_SUBTLE = "#64748B"
COLOR_MUTED = "#A1A1A1"
COLOR_SURFACE = "#F8FAFC"
COLOR_BACKGROUND = "#FFFFFF"
COLOR_ACCENT = "#3B82F6"
COLOR_SHADOW = "#00000033"
COLOR_BORDER = "#E2E8F0"

# ── DESIGN.md token registry (DESIGN.md §Color) ──────────────────────────

DESIGN_COLORS: dict[str, str] = {
    "ink": COLOR_INK,
    "subtle": COLOR_SUBTLE,
    "muted": COLOR_MUTED,
    "surface": COLOR_SURFACE,
    "background": COLOR_BACKGROUND,
    "accent": COLOR_ACCENT,
    "success": "#10B981",
    "warning": "#F59E0B",
    "error": "#EF4444",
    "border": COLOR_BORDER,
    "shadow": COLOR_SHADOW,
    # Not in DESIGN.md; light hover/variant tone matching the slate family.
    "light": "#F1F5F9",
}

# Dark-mode counterparts, aligned to the Breeze dark palette so palette-driven
# widgets and token-driven styles stay cohesive.
DESIGN_COLORS_DARK: dict[str, str] = {
    "ink": "#FCFCFC",
    "subtle": "#A0A6AC",
    "muted": "#6E767E",
    "surface": "#292C30",
    "background": "#202326",
    "accent": COLOR_ACCENT,
    "success": "#34D399",
    "warning": "#FBBF24",
    "error": "#F87171",
    "border": "#373A40",
    "shadow": "#00000066",
    "light": "#31353A",
}

# DESIGN.md §Rounded / §Spacing (px)
DESIGN_ROUNDED: dict[str, int] = {"xs": 2, "sm": 4, "md": 8, "lg": 12, "xl": 16}
DESIGN_SPACING_PX: dict[str, int] = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 20, "xxl": 24}

# DESIGN.md §Typography
DESIGN_FONT_FAMILY = '"Inter", "Segoe UI", "Noto Sans", "Ubuntu", "Cantarell", sans-serif'
DESIGN_FONT_SIZES: dict[str, int] = {"xs": 10, "sm": 12, "md": 14, "lg": 16, "xl": 20, "xxl": 24}
DESIGN_FONT_WEIGHTS: dict[str, int] = {"normal": 400, "medium": 500, "semibold": 600, "bold": 700}

_DESIGN_SPACING: dict[str, str] = {k: f"{v}px" for k, v in DESIGN_SPACING_PX.items()}

_PLACEHOLDER_RE = re.compile(r"\{(colors|spacing)\.(\w+)\}")


def _active_design_colors() -> dict[str, str]:
    """Return the design token set for the engine's current effective mode."""
    try:
        dark = get_theme_engine().effective_mode == ThemeMode.DARK
    except Exception:
        dark = False
    return DESIGN_COLORS_DARK if dark else DESIGN_COLORS


def design_color(name: str) -> str:
    """Return the DESIGN.md hex value for a color token name (e.g. 'error')."""
    return _active_design_colors().get(name, COLOR_INK)


def style(qss: str) -> str:
    """Resolve ``{colors.*}`` and ``{spacing.*}`` placeholders to DESIGN.md values.

    Unknown tokens are left as-is so problems stay visible instead of silently
    rendering a wrong color. Safe to use on strings that contain literal braces
    (QSS selectors, keyframes) because only known placeholder patterns match.
    """
    def repl(match: re.Match[str]) -> str:
        family, name = match.group(1), match.group(2)
        if family == "colors":
            return _active_design_colors().get(name, match.group(0))
        return _DESIGN_SPACING.get(name, match.group(0))

    return _PLACEHOLDER_RE.sub(repl, qss)


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
    p.setColor(QPalette.ColorRole.Highlight, QColor(59, 130, 246))  # #3B82F6 — DESIGN.md accent
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
    p.setColor(QPalette.ColorRole.Highlight, QColor(59, 130, 246))  # #3B82F6 — DESIGN.md accent
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

def _build_qss(dark: bool) -> str:
    """Build the application stylesheet from DESIGN.md tokens.

    Implements DESIGN.md §Components (button variants, inputs with focus
    states, cards, dialogs, tables) plus modern styling for tabs, menus,
    checkboxes, progress bars and scrollbars. All values come from the
    DESIGN.md token sets so light and dark stay cohesive.
    """
    c = DESIGN_COLORS_DARK if dark else DESIGN_COLORS

    def col(name: str) -> str:
        return c[name]

    accent = col("accent")
    accent_hover = "#2563EB"
    accent_pressed = "#1D4ED8"
    accent_tint = "rgba(59, 130, 246, 0.14)"
    accent_tint_strong = "rgba(59, 130, 246, 0.33)"
    ink = col("ink")
    subtle = col("subtle")
    surface = col("surface")
    background = col("background")
    border = col("border")
    error = col("error")
    handle = "rgba(252, 252, 252, 0.22)" if dark else "rgba(30, 41, 59, 0.22)"
    handle_hover = "rgba(252, 252, 252, 0.34)" if dark else "rgba(30, 41, 59, 0.34)"
    tooltip_bg = "#0F172A" if not dark else "#0B0E11"
    tooltip_fg = "#F8FAFC"

    return f"""
/* ── Base ── */
QWidget {{
    font-family: {DESIGN_FONT_FAMILY};
}}
QMainWindow, QDialog, QWizard {{
    background-color: {background};
}}
QWizardPage {{ background: transparent; }}

/* ── Typography helpers ── */
QLabel {{ color: {ink}; }}
QLabel[role="h1"] {{ font-size: 24px; font-weight: 700; }}
QLabel[role="h2"] {{ font-size: 20px; font-weight: 600; }}
QLabel[role="h3"] {{ font-size: 16px; font-weight: 600; }}
QLabel[role="caption"] {{ font-size: 10px; color: {subtle}; letter-spacing: 0.5px; }}
QLabel[role="subtle"] {{ color: {subtle}; }}

/* ── Buttons (DESIGN.md §Button) ── */
QPushButton {{
    background-color: {surface};
    color: {ink};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 8px 16px;
    font-size: 14px;
    font-weight: 500;
}}
QPushButton:hover {{
    background-color: {col("light")};
    border-color: {subtle};
}}
QPushButton:pressed {{
    background-color: {border};
}}
QPushButton:focus {{
    border-color: {accent};
}}
QPushButton:disabled {{
    background-color: {surface};
    color: {subtle};
    border-color: {border};
}}
QPushButton:default, QPushButton[variant="primary"] {{
    background-color: {accent};
    color: #FFFFFF;
    border: 1px solid {accent};
    font-weight: 600;
}}
QPushButton:default:hover, QPushButton[variant="primary"]:hover {{
    background-color: {accent_hover};
    border-color: {accent_hover};
}}
QPushButton:default:pressed, QPushButton[variant="primary"]:pressed {{
    background-color: {accent_pressed};
    border-color: {accent_pressed};
}}
QPushButton:default:disabled, QPushButton[variant="primary"]:disabled {{
    background-color: {col("muted")};
    border-color: {col("muted")};
    color: {surface};
}}
QPushButton[variant="danger"] {{
    background-color: {error};
    color: #FFFFFF;
    border: 1px solid {error};
    font-weight: 600;
}}
QPushButton[variant="danger"]:hover {{
    background-color: #DC2626;
    border-color: #DC2626;
}}
QPushButton[variant="danger"]:pressed {{
    background-color: #B91C1C;
    border-color: #B91C1C;
}}
QPushButton::menu-indicator {{
    subcontrol-origin: padding;
    subcontrol-position: right center;
    right: 8px;
}}

/* ── Inputs (DESIGN.md §Input / Text Field) ── */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox {{
    background-color: {background};
    color: {ink};
    border: 1px solid {border};
    border-radius: 4px;
    padding: 6px 12px;
    selection-background-color: {accent};
    selection-color: #FFFFFF;
    font-size: 12px;
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {accent};
}}
QLineEdit:disabled, QPlainTextEdit:disabled, QTextEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    background-color: {surface};
    color: {subtle};
    border-color: {border};
}}

/* ── Combo / Spin buttons ── */
QComboBox::drop-down, QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    border: none;
    width: 20px;
    background: transparent;
}}
QComboBox QAbstractItemView {{
    background-color: {surface};
    color: {ink};
    border: 1px solid {border};
    border-radius: 8px;
    selection-background-color: {accent_tint};
    selection-color: {ink};
    outline: none;
    padding: 4px;
}}

/* ── Check / Radio (accent when checked) ── */
QCheckBox, QRadioButton {{
    color: {ink};
    spacing: 8px;
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {subtle};
    background: {background};
}}
QCheckBox::indicator {{
    border-radius: 4px;
}}
QRadioButton::indicator {{
    border-radius: 8px;
}}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {accent};
}}
QCheckBox::indicator:checked {{
    background: {accent};
    border-color: {accent};
    image: url(:/qt-project.org/styles/commonstyle/images/standardbutton-apply-16.png);
}}
QRadioButton::indicator:checked {{
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.4, fx:0.5, fy:0.5, stop:0 #FFFFFF, stop:0.5 #FFFFFF, stop:0.6 {accent}, stop:1 {accent});
    border-color: {accent};
}}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    background: {surface};
    border-color: {border};
}}

/* ── Cards / Group boxes (DESIGN.md §Card) ── */
#card, QGroupBox {{
    background-color: {surface};
    border: 1px solid {border};
    border-radius: 12px;
    padding: 12px;
    font-size: 14px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    top: 4px;
    padding: 0 4px;
    background: {background};
    color: {ink};
    font-weight: 600;
}}

/* ── Dialogs (DESIGN.md §Dialog) ── */
QDialog QPushButton {{
    min-width: 80px;
}}

/* ── Item views (DESIGN.md §Table / List View) ── */
QTreeView, QListView, QTableView {{
    background-color: {background};
    color: {ink};
    border: 1px solid {border};
    border-radius: 8px;
    gridline-color: {border};
    outline: none;
}}
QTreeView::item, QListView::item, QTableView::item {{
    padding: 4px 8px;
    color: {ink};
}}
QTreeView::item:hover, QListView::item:hover, QTableView::item:hover {{
    background-color: {accent_tint};
}}
QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {{
    background-color: {accent_tint_strong};
    color: {ink};
}}
QTreeView::item:selected:hover, QListView::item:selected:hover {{
    background-color: {accent};
    color: #FFFFFF;
}}
QHeaderView {{
    background-color: transparent;
    border: none;
}}
QHeaderView::section {{
    background-color: {surface};
    color: {subtle};
    border: none;
    border-bottom: 1px solid {border};
    padding: 6px 8px;
    font-size: 12px;
    font-weight: 600;
}}
QTableView::item {{ padding: 6px 8px; }}

/* ── Tabs ── */
QTabWidget::pane {{
    border: 1px solid {border};
    border-radius: 8px;
    background: {background};
    top: -1px;
}}
QTabBar {{ qproperty-draggingDisabled: true; }}
QTabBar::tab {{
    padding: 8px 16px;
    border: none;
    background: transparent;
    color: {subtle};
    font-size: 14px;
    font-weight: 500;
    border-bottom: 2px solid transparent;
    margin-right: 4px;
}}
QTabBar::tab:selected {{
    color: {ink};
    font-weight: 600;
    border-bottom: 2px solid {accent};
}}
QTabBar::tab:hover:!selected {{
    color: {ink};
    background: {accent_tint};
    border-bottom-left-radius: 4px;
    border-bottom-right-radius: 4px;
}}

/* ── Menus ── */
QMenuBar {{
    background-color: {background};
    border-bottom: 1px solid {border};
    padding: 2px 8px;
}}
QMenuBar::item {{
    padding: 6px 12px;
    border-radius: 4px;
    color: {ink};
}}
QMenuBar::item:selected {{
    background-color: {accent_tint};
}}
QMenu {{
    background-color: {surface};
    color: {ink};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 6px 4px;
}}
QMenu::item {{
    padding: 8px 28px 8px 16px;
    border-radius: 4px;
    color: {ink};
    font-size: 13px;
}}
QMenu::item:selected {{
    background-color: {accent};
    color: #FFFFFF;
}}
QMenu::item:disabled {{
    color: {subtle};
}}
QMenu::separator {{
    height: 1px;
    background: {border};
    margin: 6px 8px;
}}

/* ── Tooltips ── */
QToolTip {{
    background-color: {tooltip_bg};
    color: {tooltip_fg};
    border: none;
    border-radius: 4px;
    padding: 6px 10px;
    font-size: 12px;
}}

/* ── Progress bar ── */
QProgressBar {{
    background-color: {surface};
    border: 1px solid {border};
    border-radius: 4px;
    padding: 1px;
    text-align: center;
    color: {ink};
    font-size: 11px;
}}
QProgressBar::chunk {{
    background-color: {accent};
    border-radius: 3px;
}}

/* ── Scrollbars: thin + minimal ── */
QScrollBar:vertical {{
    width: 8px;
    background: transparent;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    min-height: 20px;
    border-radius: 3px;
    background: {handle};
}}
QScrollBar::handle:vertical:hover {{
    background: {handle_hover};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
    background: transparent;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}
QScrollBar:horizontal {{
    height: 8px;
    background: transparent;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    min-width: 20px;
    border-radius: 3px;
    background: {handle};
}}
QScrollBar::handle:horizontal:hover {{
    background: {handle_hover};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
    background: transparent;
}}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: transparent;
}}
QScrollArea {{ border: none; background: transparent; }}

/* ── Status bar / Toolbar ── */
QStatusBar {{
    background-color: {background};
    color: {subtle};
    border-top: 1px solid {border};
    font-size: 12px;
}}
QStatusBar::item {{ border: none; }}
QToolBar {{
    background-color: {background};
    border: none;
    border-bottom: 1px solid {border};
    padding: 4px 8px;
    spacing: 8px;
}}
QToolBar::separator {{
    width: 1px;
    background: {border};
    margin: 4px 6px;
}}

/* ── Focus ring for keyboard a11y ── */
QComboBox:focus, QAbstractButton:focus {{
    border-color: {accent};
}}
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

    # Mapping from DESIGN.md color names to QPalette.ColorRole names
    _DESIGN_COLOR_MAP: dict[str, str] = {
        "ink": "WindowText",
        "subtle": "PlaceholderText",
        "muted": "Disabled",
        "surface": "Base",
        "background": "Window",
        "accent": "Highlight",
        "error": "BrightText",
        "warning": "BrightText",
        "success": "BrightText",
        "border": "Mid",
        "shadow": "Shadow",
    }

    def set_custom_colors(self, colors: dict[str, str]):
        """Set custom palette overrides.

        Keys can be either:
        - QPalette.ColorRole member names (e.g. "Highlight", "WindowText")
        - DESIGN.md color names (e.g. "accent", "subtle", "background")

        Values must be valid hex color strings (#RRGGBB).

        DESIGN.md color names are mapped to the nearest QPalette.ColorRole.
        Invalid keys or malformed hex values are skipped (logged) rather than
        raising, so a bad settings file can't crash startup. Call apply()
        afterwards, or just re-set `.mode` to trigger a re-apply.
        """
        validated: dict[str, str] = {}
        for role_name, hex_value in colors.items():
            # Check if it's a QPalette.ColorRole member name
            if hasattr(QPalette.ColorRole, role_name):
                if not QColor(hex_value).isValid():
                    logger.warning("Invalid color for %s in custom theme: %s", role_name, hex_value)
                    continue
                validated[role_name] = hex_value
            # Check if it's a DESIGN.md color name
            elif role_name in self._DESIGN_COLOR_MAP:
                palette_role = getattr(QPalette.ColorRole, self._DESIGN_COLOR_MAP[role_name])
                if not QColor(hex_value).isValid():
                    logger.warning("Invalid color for %s in custom theme: %s", role_name, hex_value)
                    continue
                validated[palette_role.name] = hex_value
            else:
                logger.warning("Unknown color name in custom theme: %s", role_name)
                continue
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
        # DESIGN.md typography: Inter with system UI fallbacks, body size
        font = QFont("Inter")
        font.setFamilies(["Inter", "Segoe UI", "Noto Sans", "Ubuntu", "Cantarell", "sans-serif"])
        font.setPointSizeF(10.0)
        app.setFont(font)
        # Full DESIGN.md component stylesheet, rebuilt per mode
        app.setStyleSheet(_build_qss(mode == ThemeMode.DARK))
        # Re-evaluate property-based selectors (e.g. [variant="primary"]) on
        # existing widgets — Qt drops attribute matches on stylesheet resets.
        for widget in app.allWidgets():
            w_style = widget.style()
            if w_style is not None:
                w_style.unpolish(widget)
                w_style.polish(widget)


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
