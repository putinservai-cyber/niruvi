"""Shared formatting helpers for Niruvi UI.

Uses native system palette colors instead of custom theme tokens.
"""

import datetime
import os


# ── Design Tokens (kept for layout consistency) ────────────────────────────────

MARGIN_S = 8
MARGIN_M = 12
MARGIN_L = 16
MARGIN_XL = 20

SPACING_S = 4
SPACING_M = 8
SPACING_L = 12
SPACING_XL = 16

RADIUS_XS = 2
RADIUS_S = 4
RADIUS_M = 8
RADIUS_L = 12
RADIUS_XL = 16

FONT_XS = 10
FONT_SM = 12
FONT_MD = 14
FONT_LG = 16
FONT_XL = 20
FONT_XXL = 24

MONO_FONT_STYLE = "font-family: 'JetBrains Mono', 'Fira Code', monospace; font-size: 12px;"


# ── Native Palette-Based Style Helpers ────────────────────────────────────────

def placeholder_style(text: str = "", size: int = FONT_SM) -> str:
    """Return QSS for muted placeholder-style text at the given font size."""
    return f"color: palette(placeholder-text); font-size: {size}px;"


def detail_label_style(size: int = FONT_MD) -> str:
    """Return QSS for a bold, muted detail label (used in info dialogs)."""
    return f"font-weight: bold; color: palette(placeholder-text); font-size: {size}px;"


def section_style() -> str:
    """Native QGroupBox card style using system palette."""
    return """
    QGroupBox {
        font-weight: 600;
        border: 1px solid palette(mid);
        border-radius: 8px;
        margin-top: 12px;
        padding: 16px 12px 12px 12px;
        background: palette(alternate-base);
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 12px;
        padding: 0 4px;
        background: palette(window);
        color: palette(window-text);
    }
    """


def card_style() -> str:
    """Native card style using system palette."""
    return """
    #card {
        background: palette(alternate-base);
        border: 1px solid palette(mid);
        border-radius: 8px;
        padding: 16px;
    }
    """


def tab_page_style() -> str:
    """Native QGroupBox style for wizard/tab pages."""
    return """
    QGroupBox {
        font-weight: 600;
        border: 1px solid palette(mid);
        border-radius: 8px;
        margin-top: 12px;
        padding: 16px 12px 12px 12px;
        background: palette(alternate-base);
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 12px;
        padding: 0 4px;
        background: palette(alternate-base);
        color: palette(window-text);
        border: none;
    }
    """


def sidebar_style() -> str:
    """Native sidebar nav style using system palette."""
    return """
    QListWidget {
        border: none;
        background: palette(alternate-base);
        outline: none;
        padding: 8px 0;
    }
    QListWidget::item {
        padding: 8px 12px;
        border-radius: 4px;
        margin: 1px 8px;
        color: palette(window-text);
        font-size: 13px;
    }
    QListWidget::item:selected {
        background-color: palette(highlight);
        color: palette(highlighted-text);
        font-weight: 600;
    }
    QListWidget::item:hover:!selected {
        background-color: palette(alternate-base);
    }
    """


def btn_style() -> str:
    """Native button metrics (colors come from system palette)."""
    return """
    QPushButton {
        padding: 8px 16px;
        font-size: 14px;
        font-weight: 500;
    }
    """


def error_card_style(bg_color: str, border_color: str) -> str:
    """Return QSS for a diagnostics error/warning card."""
    return f"background:{bg_color};border:1px solid {border_color};border-radius:{RADIUS_M}px;padding:{SPACING_L}px;"


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