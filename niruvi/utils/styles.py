"""Shared design tokens, QSS snippets, and formatting helpers for Niruvi UI."""

import datetime
import os

from niruvi.utils.theme_engine import style

# ── Design Tokens ──────────────────────────────────────────────────────────────

MARGIN_S = 8
MARGIN_M = 12
MARGIN_L = 16
MARGIN_XL = 20

SPACING_S = 4
SPACING_M = 8
SPACING_L = 12
SPACING_XL = 16

RADIUS_XS = 2
RADIUS_S = 4  # DESIGN.md rounded.sm — default for interactive elements
RADIUS_M = 8  # DESIGN.md rounded.md
RADIUS_L = 12  # DESIGN.md rounded.lg
RADIUS_XL = 16

FONT_XS = 10  # DESIGN.md typography scale
FONT_SM = 12
FONT_MD = 14
FONT_LG = 16
FONT_XL = 20
FONT_XXL = 24

# ── Shared QSS Snippets ───────────────────────────────────────────────────────

# ── Theme-aware style constants ────────────────────────────────────────────────
# Resolved lazily via module __getattr__ so {colors.*} tokens pick up the
# active light/dark token set at access time (PEP 562).

_STYLE_TEMPLATES: dict[str, str] = {
    "SECTION_STYLE": """
QGroupBox {
    font-weight: 600;
    border: 1px solid {colors.border};
    border-radius: 12px;
    margin-top: 12px;
    padding: 16px 12px 12px 12px;
    background: {colors.surface};
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 4px;
    background: {colors.background};
    color: {colors.ink};
}
""",
    "CARD_STYLE": """
#card {
    background: {colors.surface};
    border: 1px solid {colors.border};
    border-radius: 12px;
    padding: 16px;
}
""",
    "TAB_PAGE_STYLE": """
QGroupBox {
    font-weight: 600;
    border: 1px solid {colors.border};
    border-radius: 12px;
    margin-top: 12px;
    padding: 16px 12px 12px 12px;
    background: {colors.surface};
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 4px;
    background: {colors.surface};
    color: {colors.ink};
    border: none;
}
""",
    "SIDEBAR_STYLE": """
QListWidget {
    border: none;
    background: {colors.surface};
    outline: none;
    padding: 8px 0;
}
QListWidget::item {
    padding: 8px 12px;
    border-radius: 4px;
    margin: 1px 8px;
    color: {colors.ink};
    font-size: 13px;
}
QListWidget::item:selected {
    background-color: rgba(59, 130, 246, 0.14);
    color: {colors.accent};
    font-weight: 600;
}
QListWidget::item:hover:!selected {
    background-color: {colors.light};
}
""",
    "BTN_STYLE": """
QPushButton {
    padding: 8px 16px;
    border: 1px solid {colors.border};
    border-radius: 8px;
    background: {colors.surface};
    color: {colors.ink};
    font-size: 14px;
    font-weight: 500;
}
QPushButton:hover {
    background: {colors.light};
    border-color: {colors.subtle};
}
QPushButton:pressed {
    background: {colors.border};
}
QPushButton:focus {
    border-color: {colors.accent};
}
QPushButton:disabled {
    color: {colors.subtle};
    border-color: {colors.border};
}
""",
}

MONO_FONT_STYLE = "font-family: 'JetBrains Mono', 'Fira Code', monospace; font-size: 12px;"


def section_style() -> str:
    """Theme-aware QGroupBox card style (DESIGN.md §Card)."""
    return style(_STYLE_TEMPLATES["SECTION_STYLE"])


def card_style() -> str:
    """Theme-aware #card style (DESIGN.md §Card)."""
    return style(_STYLE_TEMPLATES["CARD_STYLE"])


def tab_page_style() -> str:
    """Theme-aware QGroupBox style for wizard/tab pages."""
    return style(_STYLE_TEMPLATES["TAB_PAGE_STYLE"])


def sidebar_style() -> str:
    """Theme-aware sidebar nav style."""
    return style(_STYLE_TEMPLATES["SIDEBAR_STYLE"])


def btn_style() -> str:
    """Theme-aware secondary button style (DESIGN.md §Button secondary)."""
    return style(_STYLE_TEMPLATES["BTN_STYLE"])


def __getattr__(name: str) -> str:
    """Legacy constant access (styles.CARD_STYLE) resolved through the active theme."""
    if name in _STYLE_TEMPLATES:
        return style(_STYLE_TEMPLATES[name])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ── Formatting Helpers ─────────────────────────────────────────────────────────


def format_size(size_bytes: int) -> str:
    """Format a byte count into a human-readable string (e.g. '1.5 MB')."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024**2:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024**3:
        return f"{size_bytes / 1024**2:.1f} MB"
    return f"{size_bytes / 1024**3:.2f} GB"


def format_dir_size(path: str) -> str:
    """Walk a directory tree and return total size as a formatted string."""
    try:
        total = 0
        if os.path.isfile(path):
            total = os.path.getsize(path)
        elif os.path.isdir(path):
            for dirpath, _, filenames in os.walk(path):
                for f in filenames:
                    try:
                        total += os.path.getsize(os.path.join(dirpath, f))
                    except OSError:
                        pass
        return format_size(total)
    except Exception:
        return "Unknown"


def format_date(timestamp: float) -> str:
    """Format a UNIX timestamp into 'YYYY-MM-DD HH:MM:SS'."""
    return datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def placeholder_style(text: str, size: int = FONT_SM) -> str:
    """Return QSS for muted placeholder-style text at the given font size."""
    return f"color: palette(placeholder-text); font-size: {size}px;"


def detail_label_style(size: int = FONT_MD) -> str:
    """Return QSS for a bold, muted detail label (used in info dialogs)."""
    return f"font-weight: bold; color: palette(placeholder-text); font-size: {size}px;"


def error_card_style(bg_color: str, border_color: str) -> str:
    """Return QSS for a diagnostics error/warning card."""
    return f"background:{bg_color};border:1px solid {border_color};border-radius:{RADIUS_M}px;padding:{SPACING_L}px;"
