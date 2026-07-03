"""Theme Engine — modern light/dark themes using Fusion style + QPalette + QSS.

Fusion style provides consistent cross-platform rendering. The palette defines
colors and a minimal QSS overlay polishes widgets (rounded buttons, focus rings,
tree/list highlighting, scroll bars, group boxes, tab widgets, menus, tooltips).
"""

import logging
from enum import Enum, auto

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

logger = logging.getLogger(__name__)


class ThemeMode(Enum):
    LIGHT = auto()
    DARK = auto()
    AUTO = auto()


# ── Light palette (modern blue-accent) ───────────────────────────────────


def _light_palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor(246, 247, 249))
    p.setColor(QPalette.ColorRole.WindowText, QColor(25, 25, 25))
    p.setColor(QPalette.ColorRole.Base, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(241, 243, 246))
    p.setColor(QPalette.ColorRole.Text, QColor(25, 25, 25))
    p.setColor(QPalette.ColorRole.Button, QColor(241, 243, 246))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(25, 25, 25))
    p.setColor(QPalette.ColorRole.Highlight, QColor(59, 130, 246))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(25, 25, 25))
    p.setColor(QPalette.ColorRole.Link, QColor(59, 130, 246))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(156, 163, 175))
    p.setColor(QPalette.ColorRole.BrightText, QColor(22, 163, 74))
    p.setColor(QPalette.ColorRole.Dark, QColor(209, 213, 219))
    p.setColor(QPalette.ColorRole.Mid, QColor(229, 231, 235))
    p.setColor(QPalette.ColorRole.Midlight, QColor(243, 244, 246))
    p.setColor(QPalette.ColorRole.Light, QColor(249, 250, 251))
    p.setColor(QPalette.ColorRole.Shadow, QColor(156, 163, 175))
    p.setColor(QPalette.ColorRole.Disabled, QPalette.ColorRole.Text, QColor(156, 163, 175))
    p.setColor(QPalette.ColorRole.Disabled, QPalette.ColorRole.ButtonText, QColor(156, 163, 175))
    return p


# ── Dark palette (modern slate-accent) ───────────────────────────────────


def _dark_palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor(24, 25, 28))
    p.setColor(QPalette.ColorRole.WindowText, QColor(229, 231, 235))
    p.setColor(QPalette.ColorRole.Base, QColor(31, 33, 37))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(38, 40, 45))
    p.setColor(QPalette.ColorRole.Text, QColor(229, 231, 235))
    p.setColor(QPalette.ColorRole.Button, QColor(38, 40, 45))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(229, 231, 235))
    p.setColor(QPalette.ColorRole.Highlight, QColor(59, 130, 246))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(38, 40, 45))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(229, 231, 235))
    p.setColor(QPalette.ColorRole.Link, QColor(96, 165, 250))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(107, 114, 128))
    p.setColor(QPalette.ColorRole.BrightText, QColor(248, 113, 113))
    p.setColor(QPalette.ColorRole.Dark, QColor(55, 58, 64))
    p.setColor(QPalette.ColorRole.Mid, QColor(55, 58, 64))
    p.setColor(QPalette.ColorRole.Midlight, QColor(68, 71, 78))
    p.setColor(QPalette.ColorRole.Light, QColor(75, 78, 85))
    p.setColor(QPalette.ColorRole.Shadow, QColor(0, 0, 0))
    p.setColor(QPalette.ColorRole.Disabled, QPalette.ColorRole.Text, QColor(107, 114, 128))
    p.setColor(QPalette.ColorRole.Disabled, QPalette.ColorRole.ButtonText, QColor(107, 114, 128))
    return p


# ── QSS overlays ─────────────────────────────────────────────────────────

_LIGHT_QSS = """
QGroupBox {
    font-weight: bold;
    border: 1px solid #e5e7eb;
    border-radius: 8px;
    margin-top: 12px;
    padding: 16px 12px 12px 12px;
    background: transparent;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 2px 8px;
    color: #6b7280;
}
QPushButton {
    padding: 6px 16px;
    border: 1px solid #d1d5db;
    border-radius: 6px;
    background: #f9fafb;
    min-height: 22px;
}
QPushButton:hover {
    background: #f3f4f6;
    border-color: #9ca3af;
}
QPushButton:pressed {
    background: #e5e7eb;
}
QPushButton:focus {
    outline: none;
    border-color: #3b82f6;
}
QComboBox {
    padding: 5px 10px;
    border: 1px solid #d1d5db;
    border-radius: 6px;
    background: #ffffff;
    min-height: 22px;
}
QComboBox:hover {
    border-color: #9ca3af;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QLineEdit {
    padding: 5px 8px;
    border: 1px solid #d1d5db;
    border-radius: 6px;
    background: #ffffff;
    selection-background-color: #3b82f6;
}
QLineEdit:focus {
    border-color: #3b82f6;
}
QTreeView, QListView, QTableView {
    border: 1px solid #e5e7eb;
    border-radius: 6px;
    background: #ffffff;
    alternate-background-color: #f9fafb;
    outline: none;
}
QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {
    background: #3b82f6;
    color: white;
}
QTreeView::item:hover, QListView::item:hover {
    background: #f3f4f6;
}
QTreeView::item, QListView::item {
    padding: 4px 6px;
}
QHeaderView::section {
    background: #f9fafb;
    border: none;
    border-bottom: 1px solid #e5e7eb;
    border-right: 1px solid #e5e7eb;
    padding: 6px 8px;
    font-weight: bold;
    color: #6b7280;
}
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #d1d5db;
    border-radius: 5px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: #9ca3af;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background: #d1d5db;
    border-radius: 5px;
    min-width: 30px;
}
QScrollBar::handle:horizontal:hover {
    background: #9ca3af;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}
QProgressBar {
    border: 1px solid #e5e7eb;
    border-radius: 6px;
    background: #f3f4f6;
    text-align: center;
    min-height: 16px;
}
QProgressBar::chunk {
    background: #3b82f6;
    border-radius: 5px;
}
QTabWidget::pane {
    border: 1px solid #e5e7eb;
    border-radius: 6px;
    background: white;
}
QTabBar::tab {
    padding: 6px 16px;
    border: 1px solid transparent;
    border-bottom: 2px solid transparent;
    color: #6b7280;
}
QTabBar::tab:selected {
    color: #3b82f6;
    border-bottom: 2px solid #3b82f6;
}
QTabBar::tab:hover:!selected {
    color: #374151;
    background: #f9fafb;
}
QMenu {
    background: white;
    border: 1px solid #e5e7eb;
    border-radius: 8px;
    padding: 4px;
}
QMenu::item {
    padding: 6px 24px;
    border-radius: 4px;
}
QMenu::item:selected {
    background: #3b82f6;
    color: white;
}
QMenu::separator {
    height: 1px;
    background: #e5e7eb;
    margin: 4px 8px;
}
QToolTip {
    background: #1f2937;
    color: white;
    border: none;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
}
QStatusBar {
    background: #f9fafb;
    border-top: 1px solid #e5e7eb;
}
QSplitter::handle {
    background: #e5e7eb;
}
QSplitter::handle:hover {
    background: #9ca3af;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border: 2px solid #d1d5db;
    border-radius: 4px;
    background: white;
}
QCheckBox::indicator:checked {
    background: #3b82f6;
    border-color: #3b82f6;
}
QRadioButton::indicator {
    width: 18px;
    height: 18px;
    border: 2px solid #d1d5db;
    border-radius: 9px;
    background: white;
}
QRadioButton::indicator:checked {
    background: #3b82f6;
    border-color: #3b82f6;
}
"""

_DARK_QSS = """
QGroupBox {
    font-weight: bold;
    border: 1px solid #374151;
    border-radius: 8px;
    margin-top: 12px;
    padding: 16px 12px 12px 12px;
    background: transparent;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 2px 8px;
    color: #9ca3af;
}
QPushButton {
    padding: 6px 16px;
    border: 1px solid #4b5563;
    border-radius: 6px;
    background: #2d3139;
    min-height: 22px;
    color: #e5e7eb;
}
QPushButton:hover {
    background: #384050;
    border-color: #6b7280;
}
QPushButton:pressed {
    background: #374151;
}
QPushButton:focus {
    outline: none;
    border-color: #60a5fa;
}
QComboBox {
    padding: 5px 10px;
    border: 1px solid #4b5563;
    border-radius: 6px;
    background: #2d3139;
    color: #e5e7eb;
    min-height: 22px;
}
QComboBox:hover {
    border-color: #6b7280;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox QAbstractItemView {
    background: #1f2128;
    color: #e5e7eb;
    border: 1px solid #4b5563;
    border-radius: 6px;
    selection-background-color: #3b82f6;
}
QLineEdit {
    padding: 5px 8px;
    border: 1px solid #4b5563;
    border-radius: 6px;
    background: #1f2128;
    color: #e5e7eb;
    selection-background-color: #3b82f6;
}
QLineEdit:focus {
    border-color: #60a5fa;
}
QTreeView, QListView, QTableView {
    border: 1px solid #374151;
    border-radius: 6px;
    background: #1f2128;
    alternate-background-color: #252830;
    color: #e5e7eb;
    outline: none;
}
QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {
    background: #3b82f6;
    color: white;
}
QTreeView::item:hover, QListView::item:hover {
    background: #2d3139;
}
QTreeView::item, QListView::item {
    padding: 4px 6px;
}
QHeaderView::section {
    background: #252830;
    border: none;
    border-bottom: 1px solid #374151;
    border-right: 1px solid #374151;
    padding: 6px 8px;
    font-weight: bold;
    color: #9ca3af;
}
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #4b5563;
    border-radius: 5px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: #6b7280;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background: #4b5563;
    border-radius: 5px;
    min-width: 30px;
}
QScrollBar::handle:horizontal:hover {
    background: #6b7280;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}
QProgressBar {
    border: 1px solid #374151;
    border-radius: 6px;
    background: #2d3139;
    text-align: center;
    color: #e5e7eb;
    min-height: 16px;
}
QProgressBar::chunk {
    background: #3b82f6;
    border-radius: 5px;
}
QTabWidget::pane {
    border: 1px solid #374151;
    border-radius: 6px;
    background: #1f2128;
}
QTabBar::tab {
    padding: 6px 16px;
    border: 1px solid transparent;
    border-bottom: 2px solid transparent;
    color: #9ca3af;
}
QTabBar::tab:selected {
    color: #60a5fa;
    border-bottom: 2px solid #60a5fa;
}
QTabBar::tab:hover:!selected {
    color: #e5e7eb;
    background: #2d3139;
}
QMenu {
    background: #1f2128;
    border: 1px solid #374151;
    border-radius: 8px;
    padding: 4px;
    color: #e5e7eb;
}
QMenu::item {
    padding: 6px 24px;
    border-radius: 4px;
}
QMenu::item:selected {
    background: #3b82f6;
    color: white;
}
QMenu::separator {
    height: 1px;
    background: #374151;
    margin: 4px 8px;
}
QToolTip {
    background: #374151;
    color: #e5e7eb;
    border: none;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
}
QStatusBar {
    background: #252830;
    border-top: 1px solid #374151;
    color: #9ca3af;
}
QSplitter::handle {
    background: #374151;
}
QSplitter::handle:hover {
    background: #6b7280;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border: 2px solid #4b5563;
    border-radius: 4px;
    background: #1f2128;
}
QCheckBox::indicator:checked {
    background: #3b82f6;
    border-color: #3b82f6;
}
QRadioButton::indicator {
    width: 18px;
    height: 18px;
    border: 2px solid #4b5563;
    border-radius: 9px;
    background: #1f2128;
}
QRadioButton::indicator:checked {
    background: #3b82f6;
    border-color: #3b82f6;
}
"""


class ThemeEngine:
    """Applies light/dark Fusion theme with system detection for auto mode."""

    def __init__(self):
        self._mode = ThemeMode.AUTO
        self._listeners: list[callable] = []

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

    def on_theme_changed(self, callback: callable):
        self._listeners.append(callback)

    def _notify(self):
        for cb in self._listeners:
            try:
                cb(self._mode)
            except Exception as e:
                logger.warning("Theme listener error: %s", e)

    def _detect_system_theme(self) -> ThemeMode:
        try:
            import subprocess

            result = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if "dark" in result.stdout.lower():
                return ThemeMode.DARK
        except Exception:
            pass
        try:
            result = subprocess.run(
                ["kreadconfig6", "--group", "General", "--key", "ColorScheme", "--default", "Breeze"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if "dark" in result.stdout.lower():
                return ThemeMode.DARK
        except Exception:
            pass
        return ThemeMode.LIGHT

    def apply(self):
        app = QApplication.instance()
        if app is None:
            return
        app.setStyle("Fusion")
        mode = self.effective_mode
        if mode == ThemeMode.DARK:
            app.setPalette(_dark_palette())
            app.setStyleSheet(_DARK_QSS)
        else:
            app.setPalette(_light_palette())
            app.setStyleSheet(_LIGHT_QSS)


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
    QTimer.singleShot(0, engine.apply)
