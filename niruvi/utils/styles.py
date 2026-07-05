"""Shared design tokens, QSS snippets, and formatting helpers for Niruvi UI."""

import datetime
import os

# ── Design Tokens ──────────────────────────────────────────────────────────────

MARGIN_S = 8
MARGIN_M = 12
MARGIN_L = 16
MARGIN_XL = 20

SPACING_S = 4
SPACING_M = 8
SPACING_L = 12
SPACING_XL = 16

RADIUS_S = 2
RADIUS_M = 4
RADIUS_L = 8

FONT_SM = 11
FONT_MD = 12
FONT_LG = 13
FONT_XL = 14
FONT_XXL = 18

# ── Shared QSS Snippets ───────────────────────────────────────────────────────

SECTION_STYLE = """
QGroupBox {{
    font-weight: bold;
    border: 1px solid palette(mid);
    border-radius: 6px;
    margin-top: 8px;
    padding-top: 14px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 2px 8px;
    background: palette(window);
}}
"""

CARD_STYLE = """
#card {
    background: palette(window);
    border: 1px solid palette(midlight);
    border-radius: 8px;
    padding: 16px;
}
"""

TAB_PAGE_STYLE = """
QGroupBox {
    font-weight: bold;
    border: 1px solid palette(mid);
    border-radius: 8px;
    margin-top: 10px;
    padding: 16px 12px 12px 12px;
    background: palette(window);
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 2px 10px;
    background: palette(window);
    border: none;
}
"""

SIDEBAR_STYLE = """
QListWidget {
    border: none;
    background: palette(window);
    outline: none;
    padding: 4px 0;
}
QListWidget::item {
    padding: 8px 16px;
    border-radius: 6px;
    margin: 1px 4px;
}
QListWidget::item:selected {
    background-color: palette(highlight);
    color: palette(highlighted-text);
}
QListWidget::item:hover:!selected {
    background-color: palette(midlight);
}
"""

MONO_FONT_STYLE = "font-family: monospace; font-size: 10pt;"

BTN_STYLE = """
QPushButton {
    padding: 6px 16px;
    border: 1px solid palette(mid);
    border-radius: 4px;
    background: palette(button);
    font-size: 12px;
}
QPushButton:hover {
    background: palette(light);
    border-color: palette(highlight);
}
QPushButton:pressed {
    background: palette(midlight);
}
"""


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
