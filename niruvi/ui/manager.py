import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PyQt6.QtCore import QEventLoop, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QDragEnterEvent, QDropEvent, QIcon, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QSystemTrayIcon,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from niruvi._version import __app_name__
from niruvi.app.background_updater import BackgroundUpdater
from niruvi.app.health_check import check_app_health
from niruvi.app.self_update import check_for_updates
from niruvi.app.tags import DEFAULT_CATEGORIES
from niruvi.app.update_sources import resolve_update_source
from niruvi.core.broker import get_permission_store as _get_perm_store
from niruvi.core.hooks import ensure_hooks_dir, run_hooks
from niruvi.core.repair import repair_full
from niruvi.core.verification import verify_complete
from niruvi.core.worker import ExtractionWorker, InstallQueueWorker, _ensure_local, _is_removable_path, start_worker
from niruvi.desktop.appimage_assets import extract_metadata
from niruvi.desktop.appimage_metadata import AppImageMetadata
from niruvi.desktop.desktop_utils import (
    create_desktop_entry,
    create_desktop_shortcut,
    find_desktop_for_app,
    find_desktop_shortcut,
    get_version,
    parse_desktop_file,
    register_mime_handler,
)
from niruvi.desktop.icon_utils import app_icon, to_png_bytes
from niruvi.desktop.installation_registry import InstallationRecord, InstallationRegistry
from niruvi.ui.app_info_dialog import AppInfoDialog
from niruvi.ui.build_wizard import BuildWizard
from niruvi.ui.device_info import DeviceInfoDialog
from niruvi.ui.help_dialog import HelpDialog
from niruvi.ui.report_page import ReportPage
from niruvi.ui.settings import (
    DEFAULT_INSTALL_DIR,
    SettingsDialog,
    _settings,
    get_settings,
)
from niruvi.ui.uninstall_dialog import UninstallWizard
from niruvi.ui.widgets import (
    ICON_BOX_CARD,
    ICON_SIZE_CARD,
    ICON_SIZE_EMPTY,
    ICON_SIZE_HERO,
    ICON_SIZE_TOOLBAR,
    ElidingLabel,
    PlaceholderWidget,
)
from niruvi.ui.wizard import InstallWizard
from niruvi.utils import get_icon, icon_pixmap, refresh_icons
from niruvi.utils.sound_manager import cleanup as sound_cleanup
from niruvi.utils.sound_manager import play as play_sound
from niruvi.utils.sound_manager import play_and
from niruvi.utils.styles import FONT_MD, FONT_SM, format_size
from niruvi.utils.theme_engine import (
    COLOR_ERROR,
    COLOR_INK,
    COLOR_WARNING,
    get_theme_engine,
)

logger = logging.getLogger(__name__)

_DETACHED: list[subprocess.Popen] = []


def _prune_detached():
    for i in reversed(range(len(_DETACHED))):
        p = _DETACHED[i]
        if p.poll() is not None:
            del _DETACHED[i]


_append_p = _DETACHED.append


def _track_detached(p: subprocess.Popen):
    _prune_detached()
    _append_p(p)


def _get_system_info_for_dialog() -> str:
    import platform

    lines = []
    lines.append(f"<b>Distribution:</b> {platform.system()} {platform.release()}")
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    lines.append(f"<b>OS:</b> {line.split('=', 1)[1].strip().strip(chr(34))}")
                    break
    except Exception as e:
        logger.debug("Failed to read os-release: %s", e, exc_info=True)
    lines.append(f"<b>Kernel:</b> {platform.version()}")
    try:
        result = subprocess.run(["glxinfo", "-B"], capture_output=True, text=True, timeout=5)
        for line in result.stdout.splitlines():
            if "OpenGL renderer" in line:
                lines.append(f"<b>GPU:</b> {line.split(':', 1)[1].strip()}")
                break
    except Exception as e:
        logger.debug("Failed to get GPU info: %s", e, exc_info=True)
    try:
        result = subprocess.run(
            ["rpm", "-q", "mesa-dri-drivers", "--qf", "%{VERSION}"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            lines.append(f"<b>Mesa:</b> {result.stdout.strip()}")
    except Exception as e:
        logger.debug("Failed to query Mesa: %s", e, exc_info=True)
    return "<br>".join(lines)


def _get_app_suggestions(app_name: str) -> list[tuple[str, str]]:
    """Returns list of (label, suggestion) tuples for common run fixes."""
    suggestions = []
    low = app_name.lower()
    if "chrome" in low or "chromium" in low or "brave" in low or "edge" in low:
        suggestions.append(("Disable GPU", "--disable-gpu"))
        suggestions.append(("Disable sandbox", "--no-sandbox"))
        suggestions.append(("Disable setuid sandbox", "--disable-setuid-sandbox"))
    if "electron" in low or "slack" in low or "discord" in low or "code" in low or "team" in low:
        suggestions.append(("Disable GPU", "--disable-gpu"))
        suggestions.append(("Disable sandbox", "--no-sandbox"))
    suggestions.append(("Force software rendering", "LIBGL_ALWAYS_SOFTWARE=1"))
    suggestions.append(("Skip FUSE (extract & run)", "--appimage-extract-and-run"))
    return suggestions


# Shared button height so a row of toolbar buttons lines up regardless of the
# individual stylesheet padding each one sets.
CONTROL_HEIGHT = 34


def _standardise_button(button, accessible_name: str | None = None):
    """Give a toolbar/dialog button consistent metrics and an accessible name."""
    button.setMinimumHeight(CONTROL_HEIGHT)
    button.setIconSize(QSize(ICON_SIZE_TOOLBAR, ICON_SIZE_TOOLBAR))
    if accessible_name:
        # Screen readers and "accessible name" audits ignore the visible text
        # when a tooltip is the only description, so set both.
        button.setAccessibleName(accessible_name)
    return button


class AppListItemWidget(QWidget):
    """Custom two-line widget for the installed apps list."""

    def __init__(
        self, icon: QIcon, name: str, key: str, version: str, size: int, health_info: dict | None = None, parent=None
    ):
        super().__init__(parent)
        self.setAutoFillBackground(True)
        self._full_name = name
        self._key = key
        self._size = size
        self._health_info = health_info or {}
        self._full_meta = " · ".join(filter(None, [key if key != name else "", format_size(size) if size else ""]))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 8, 4)
        layout.setSpacing(10)

        # A missing or undecodable icon must still render something recognisable
        # rather than an empty 36px hole in the row.
        if icon is None or icon.isNull():
            icon = app_icon(None, ICON_SIZE_CARD)

        self._icon = icon
        icon_label = QLabel()
        icon_label.setFixedSize(ICON_BOX_CARD, ICON_BOX_CARD)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setPixmap(icon_pixmap(icon, ICON_SIZE_CARD))
        icon_label.setAccessibleName(f"{name} icon")
        self._icon_label = icon_label
        layout.addWidget(icon_label)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(1)
        text_layout.setContentsMargins(0, 0, 0, 0)

        name_row = QHBoxLayout()
        name_row.setSpacing(6)
        name_label = ElidingLabel(name)
        name_label.setToolTip(name)
        name_label.setStyleSheet(f"font-weight: bold; font-size: {FONT_MD}px;")
        name_row.addWidget(name_label, 1)

        if version:
            ver_badge = QLabel(f"v{version}")
            ver_badge.setStyleSheet(
                f"color: palette(placeholder-text); font-size: {FONT_SM}px;"
                "padding: 1px 6px; border: 1px solid palette(mid); border-radius: 4px;"
            )
            ver_badge.setToolTip(f"Installed version {version}")
            name_row.addWidget(ver_badge, 0)
        name_row.addStretch()
        text_layout.addLayout(name_row)

        meta_parts = []
        if key != name:
            meta_parts.append(key)
        if size > 0:
            meta_parts.append(format_size(size))
        if self._health_info:
            issues = self._health_info.get("issues", [])
            warnings = self._health_info.get("warnings", [])
            if issues:
                meta_parts.append(f"{'issue' if len(issues) == 1 else 'issues'}: {', '.join(issues)}")
            if warnings:
                meta_parts.append(f"{'warning' if len(warnings) == 1 else 'warnings'}: {', '.join(warnings)}")
        if meta_parts:
            meta_text = " \u00b7 ".join(meta_parts)
            self._meta_label = ElidingLabel(meta_text)
            self._meta_label.setToolTip(meta_text)
            # Theme tokens rather than literal hex, so warnings stay legible in
            # both light and dark mode.
            meta_style = f"font-size: {FONT_SM}px;"
            if self._health_info.get("issues"):
                meta_style += f" color: {COLOR_ERROR};"
            elif self._health_info.get("warnings"):
                meta_style += f" color: {COLOR_WARNING};"
            else:
                meta_style += " color: palette(placeholder-text);"
            self._meta_label.setStyleSheet(meta_style)
            text_layout.addWidget(self._meta_label)
        else:
            self._meta_label = None

        layout.addLayout(text_layout, 1)

    def set_size(self, size: int) -> None:
        """Update the size shown in the meta row once it is known."""
        self._size = size
        meta_label = getattr(self, "_meta_label", None)
        if meta_label is None:
            return
        parts = []
        if self._key != self._full_name:
            parts.append(self._key)
        if size > 0:
            parts.append(format_size(size))
        issues = self._health_info.get("issues", [])
        warnings = self._health_info.get("warnings", [])
        if issues:
            parts.append(f"{'issue' if len(issues) == 1 else 'issues'}: {', '.join(issues)}")
        if warnings:
            parts.append(f"{'warning' if len(warnings) == 1 else 'warnings'}: {', '.join(warnings)}")
        if parts:
            text = " \u00b7 ".join(parts)
            meta_label.setToolTip(text)
            meta_label.setText(text)

    def refresh_icon(self) -> None:
        """Re-render the row icon for the current theme.

        The pixmap is baked at construction, so a light/dark switch would
        otherwise leave the old colour behind (white-on-white in light mode).
        The stored QIcon re-reads the recoloured file, so re-rendering fixes it.
        """
        if getattr(self, "_icon", None) is not None:
            self._icon_label.setPixmap(icon_pixmap(self._icon, ICON_SIZE_CARD))


class MetadataWorker(QThread):
    metadata_ready = pyqtSignal(dict, object)

    def __init__(self, appimage_path, parent=None):
        super().__init__(parent)
        self.appimage_path = appimage_path

    def run(self):
        info, icon_data = get_appimage_metadata(self.appimage_path)
        self.metadata_ready.emit(info, icon_data)


class ListSizeWorker(QThread):
    """Computes install sizes off the main thread, one app at a time.

    Directory walks are the most expensive thing the app list does; without
    this worker a single uncached app would freeze painting on every refresh.
    """

    size_ready = pyqtSignal(str, int)  # app_key, size bytes (0 when unavailable)

    def __init__(self, targets: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self._targets = targets
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        from niruvi.core.storage import app_dir_size

        for key, app_dir in self._targets:
            if self._stop:
                break
            size = app_dir_size(app_dir) if app_dir and os.path.isdir(app_dir) else 0
            self.size_ready.emit(key, size)


def get_appimage_metadata(path: str) -> tuple[dict, bytes | None]:
    """Extract metadata and icon from an AppImage. Returns (info_dict, icon_bytes)."""
    info = {"Name": Path(path).stem}
    icon_data = None
    try:
        meta = AppImageMetadata(path)
        info["Architecture"] = meta.architecture
        info["Type"] = f"Type{meta.type}"
    except Exception as e:
        logging.debug("Could not parse AppImage metadata for %s: %s", path, e)
    try:
        with tempfile.TemporaryDirectory(prefix="aim-meta-") as tmp:
            assets = extract_metadata(path, tmp)
            desktop_path = assets.get("desktop")
            if desktop_path:
                info.update(parse_desktop_file(desktop_path))
            icon_path = assets.get("icon")
            if icon_path:
                data = Path(icon_path).read_bytes()
                png = to_png_bytes(data)
                if png:
                    icon_data = png
    except Exception as e:
        logging.debug("Could not extract assets from %s: %s", path, e)
    return info, icon_data


class Niruvi(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Niruvi")
        self.setMinimumSize(780, 560)
        self.setAcceptDrops(True)
        self.installed_apps: dict = {}
        self.worker: ExtractionWorker | None = None
        self._metadata_worker: MetadataWorker | None = None
        self._background_updater = BackgroundUpdater(self)
        self._background_updater.update_found.connect(self._on_background_update_found)
        self._batch_worker: InstallQueueWorker | None = None
        self._size_worker: ListSizeWorker | None = None
        self._pending_sizes: list[tuple[str, str]] = []
        self._tray_icons: list[QSystemTrayIcon] = []
        self._tray: QSystemTrayIcon | None = None
        self._really_quit = False
        ensure_hooks_dir()
        self._is_scanning = False
        self._sort_index = 0
        self._init_ui()
        self.scan_installed()
        self._start_background_updater()
        self._sync_tray()
        from niruvi.core.plugins import reload_plugins

        reload_plugins()

    def _show_status(self, msg: str):
        if self._status_bar is not None:
            self._status_bar.showMessage(msg)

    def _add_action(self, menu: QMenu, icon: QIcon, text: str) -> QAction:
        action = menu.addAction(icon, text)
        assert action is not None
        return action

    def _add_action_text(self, menu: QMenu, text: str) -> QAction:
        action = menu.addAction(text)
        assert action is not None
        return action

    def _add_menu(self, title: str) -> QMenu:
        menubar = self.menuBar()
        assert menubar is not None
        menu = menubar.addMenu(title)
        assert menu is not None
        return menu

    def _init_ui(self):
        self._create_menu_bar()
        self._create_toolbar()
        self._create_central_widget()

        QShortcut("Ctrl+U", self).activated.connect(lambda: self._shortcut_uninstall())
        QShortcut("Ctrl+D", self).activated.connect(lambda: self._shortcut_app_info())
        QShortcut("Ctrl+O", self).activated.connect(lambda: self._shortcut_open_folder())

        self._status_bar = self.statusBar()
        self.setStatusBar(self._status_bar)
        self._show_status("Ready")

        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.paletteChanged.connect(self._refresh_themed_icons)

    def _create_menu_bar(self):
        file_menu = self._add_menu("File")

        install_action = QAction(get_icon("list-add"), "Install...", self)
        install_action.setShortcut("Ctrl+I")
        install_action.triggered.connect(lambda: play_and("click", self.run_install_wizard))
        file_menu.addAction(install_action)

        settings_action = QAction(get_icon("preferences-system"), "Settings...", self)
        settings_action.triggered.connect(lambda: play_and("click", self.open_settings))
        file_menu.addAction(settings_action)

        file_menu.addSeparator()

        export_action = QAction(get_icon("document-save-as"), "Export App List...", self)
        export_action.triggered.connect(lambda: play_and("click", self._export_app_list))
        file_menu.addAction(export_action)

        import_action = QAction(get_icon("document-open"), "Import App List...", self)
        import_action.triggered.connect(lambda: play_and("click", self._import_app_list))
        file_menu.addAction(import_action)

        file_menu.addSeparator()

        quit_action = QAction(get_icon("application-exit"), "Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(lambda: play_and("navigation", self.close))
        file_menu.addAction(quit_action)

        tools_menu = self._add_menu("Tools")

        store_action = QAction(
            get_icon("applications-internet", "folder-download", "emblem-downloads"),
            "App Store...",
            self,
        )
        store_action.setShortcut("Ctrl+Shift+S")
        store_action.triggered.connect(self._open_store)
        tools_menu.addAction(store_action)

        build_action = QAction(
            get_icon("emblem-system", "applications-utilities", "document-export"), "Build AppImage...", self
        )
        build_action.setShortcut("Ctrl+B")
        build_action.triggered.connect(self._open_build_dialog)
        tools_menu.addAction(build_action)

        device_action = QAction(get_icon("computer", "video-display", "system-search"), "Device Info...", self)
        device_action.triggered.connect(self._show_device_info)
        tools_menu.addAction(device_action)

        storage_action = QAction(get_icon("drive-harddisk", "folder"), "Storage Analyzer...", self)
        storage_action.triggered.connect(self._show_storage_analyzer)
        tools_menu.addAction(storage_action)

        tools_menu.addSeparator()

        check_updates_action = QAction(
            get_icon("emblem-downloads", "system-software-update", "download", "document-save"),
            "Check for Niruvi Updates...",
            self,
        )
        check_updates_action.triggered.connect(lambda: check_for_updates(self))
        tools_menu.addAction(check_updates_action)

        check_all_action = QAction(
            get_icon("network-server", "emblem-downloads"), "Check All Apps for Updates...", self
        )
        check_all_action.triggered.connect(self._check_all_app_updates)
        tools_menu.addAction(check_all_action)

        refresh_action = QAction(get_icon("view-refresh"), "Refresh Installed", self)
        refresh_action.setShortcut("Ctrl+R")
        refresh_action.triggered.connect(self.scan_installed)
        tools_menu.addAction(refresh_action)

        help_menu = self._add_menu("Help")

        help_action = QAction(get_icon("help-contents"), "Help...", self)
        help_action.setShortcut("F1")
        help_action.triggered.connect(self._show_help)
        help_menu.addAction(help_action)

        report_action = QAction(get_icon("bug", "tools-report-bug", "dialog-warning"), "Report Issue...", self)
        report_action.triggered.connect(self._show_report_page)
        help_menu.addAction(report_action)

        help_menu.addSeparator()

        license_action = QAction(get_icon("emblem-documents", "help-about", "document-properties"), "License", self)
        license_action.triggered.connect(self._show_license)
        help_menu.addAction(license_action)

        help_menu.addSeparator()

        about_action = QAction(get_icon("help-about"), "About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

        help_menu.addSeparator()

        support_action = QAction(get_icon("emblem-favorite", "favorites", "starred"), "Support Niruvi", self)
        support_action.triggered.connect(self._show_support)
        help_menu.addAction(support_action)

    def _create_toolbar(self):
        toolbar = QToolBar("Main Toolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(ICON_SIZE_TOOLBAR, ICON_SIZE_TOOLBAR))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)

        self.action_install = QAction(get_icon("list-add"), "Install", self)
        self.action_install.setShortcut("Ctrl+I")
        self.action_install.setToolTip("Install an AppImage file (Ctrl+I)")
        self.action_install.triggered.connect(lambda: play_and("click", self.run_install_wizard))
        toolbar.addAction(self.action_install)

        self.action_build = QAction(get_icon("emblem-system"), "Build", self)
        self.action_build.setShortcut("Ctrl+B")
        self.action_build.setToolTip("Build an AppImage from a package (Ctrl+B)")
        self.action_build.triggered.connect(lambda: play_and("click", self._open_build_dialog))
        toolbar.addAction(self.action_build)

        self.action_refresh = QAction(get_icon("view-refresh"), "Refresh", self)
        self.action_refresh.setShortcut("Ctrl+R")
        self.action_refresh.setToolTip("Re-scan installed apps (Ctrl+R)")
        self.action_refresh.triggered.connect(lambda: play_and("click", self.scan_installed))
        toolbar.addAction(self.action_refresh)

        self.action_store = QAction(get_icon("applications-internet"), "App Store", self)
        self.action_store.setShortcut("Ctrl+Shift+S")
        self.action_store.setToolTip("Browse the App Store (Ctrl+Shift+S)")
        self.action_store.triggered.connect(self._open_store)
        toolbar.addAction(self.action_store)

        toolbar.addSeparator()

        self.action_batch = QAction(get_icon("list-add"), "Batch", self)
        self.action_batch.setToolTip("Batch actions on multiple selected apps (Ctrl+Click to multi-select)")
        self.action_batch.setEnabled(False)
        batch_menu = QMenu(self)
        act_batch_update = self._add_action(batch_menu, get_icon("emblem-downloads"), "Update Selected...")
        act_batch_update.triggered.connect(self._batch_update_selected)
        act_batch_verify = self._add_action(batch_menu, get_icon("security-high", "dialog-ok"), "Verify Selected")
        act_batch_verify.triggered.connect(self._batch_verify_selected)
        act_batch_uninstall = self._add_action(batch_menu, get_icon("edit-delete"), "Uninstall Selected...")
        act_batch_uninstall.triggered.connect(self._batch_uninstall_selected)
        self.action_batch.setMenu(batch_menu)
        toolbar.addAction(self.action_batch)

        toolbar.addSeparator()

        self.action_check_updates = QAction(get_icon("system-software-update"), "Check Updates", self)
        self.action_check_updates.setToolTip("Check all apps for updates")
        self.action_check_updates.triggered.connect(self._check_all_app_updates)
        toolbar.addAction(self.action_check_updates)

        self.action_settings = QAction(get_icon("preferences-system"), "Settings", self)
        self.action_settings.setToolTip("Open settings")
        self.action_settings.triggered.connect(lambda: play_and("click", self.open_settings))
        toolbar.addAction(self.action_settings)

    def _create_central_widget(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        # Header with count and actions
        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)
        self.installed_count_label = QLabel("No apps installed")
        self.installed_count_label.setStyleSheet("font-weight: 600; font-size: 14px; color: palette(window-text);")
        self.installed_count_label.setAccessibleName("Installed application count")
        header_layout.addWidget(self.installed_count_label)
        header_layout.addStretch()
        layout.addLayout(header_layout)

        # Search and filter bar
        search_filter_layout = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search installed apps...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setToolTip("Type to filter the app list by name, version, or category")
        self.search_edit.setAccessibleName("Search installed applications")
        self.search_edit.addAction(get_icon("edit-find"), QLineEdit.ActionPosition.LeadingPosition)
        self.search_edit.textChanged.connect(self._filter_apps)
        search_filter_layout.addWidget(self.search_edit)

        self.sort_combo = QComboBox()
        self.sort_combo.addItems([
            "Sort by Name",
            "Sort by Size",
            "Sort by Install Date",
            "Sort by Category",
        ])
        self.sort_combo.currentIndexChanged.connect(self._sort_apps)
        self.sort_combo.setToolTip("Change how installed apps are ordered")
        self.sort_combo.setAccessibleName("Sort installed applications")
        self.sort_combo.setMinimumHeight(CONTROL_HEIGHT)
        search_filter_layout.addWidget(self.sort_combo)

        self.category_combo = QComboBox()
        self.category_combo.addItem("All Categories")
        for cat in DEFAULT_CATEGORIES:
            self.category_combo.addItem(cat)
        self.category_combo.addItem("Uncategorized")
        self.category_combo.currentIndexChanged.connect(lambda _: self._filter_apps(self.search_edit.text()))
        self.category_combo.setToolTip("Filter apps by category tag")
        self.category_combo.setAccessibleName("Filter by category")
        self.category_combo.setMinimumHeight(CONTROL_HEIGHT)
        self.category_combo.setMinimumWidth(120)
        self.category_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        search_filter_layout.addWidget(self.category_combo)
        layout.addLayout(search_filter_layout)

        # App list
        self.installed_list = QListWidget()
        self.installed_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.installed_list.itemSelectionChanged.connect(self._update_batch_button)
        self.installed_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.installed_list.customContextMenuRequested.connect(self._show_context_menu)
        self.installed_list.setIconSize(QSize(ICON_SIZE_CARD, ICON_SIZE_CARD))
        self.installed_list.setSpacing(4)
        self.installed_list.setFrameShape(QFrame.Shape.NoFrame)
        self.installed_list.itemDoubleClicked.connect(self._on_app_double_clicked)
        self.installed_list.setAccessibleName("Installed applications")
        self.installed_list.setToolTip("Installed applications. Double-click or press Enter to launch.")
        layout.addWidget(self.installed_list, 1)

        # Empty state
        self.empty_widget = QWidget()
        self.empty_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        empty_layout = QVBoxLayout(self.empty_widget)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.setSpacing(16)

        empty_icon_label = QLabel()
        empty_art = get_icon("package-x-generic", "application-x-archive", "application-x-executable")
        empty_icon_label.setPixmap(icon_pixmap(empty_art, ICON_SIZE_EMPTY))
        empty_icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_icon = empty_art
        self._empty_icon_label = empty_icon_label
        empty_layout.addWidget(empty_icon_label)

        empty_title = QLabel("<h2>No AppImages Installed</h2>")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_title)

        empty_desc = QLabel("Drag and drop an AppImage file here to install it.\n\nOr click the button below to browse for a file.")
        empty_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_desc.setWordWrap(True)
        empty_desc.setStyleSheet("color: palette(placeholder-text);")
        empty_layout.addWidget(empty_desc)

        self.empty_install_btn = QPushButton(get_icon("list-add"), "  Install AppImage")
        self.empty_install_btn.setFixedWidth(220)
        self.empty_install_btn.setMinimumHeight(40)
        self.empty_install_btn.clicked.connect(self.run_install_wizard)
        btn_wrapper = QHBoxLayout()
        btn_wrapper.addStretch()
        btn_wrapper.addWidget(self.empty_install_btn)
        btn_wrapper.addStretch()
        empty_layout.addLayout(btn_wrapper)

        layout.addWidget(self.empty_widget, 1)

        # No results state
        self.no_results_widget = PlaceholderWidget(
            "No Matching Applications",
            "No installed application matches the current search text or category filter.",
        )
        self.no_results_widget.set_icon(get_icon("system-search"), ICON_SIZE_EMPTY)
        self.no_results_widget.setVisible(False)
        layout.addWidget(self.no_results_widget, 1)

        # Set initial state
        self.installed_list.setVisible(False)
        self.search_edit.setVisible(False)
        self.sort_combo.setVisible(False)
        self.category_combo.setVisible(False)
        self.empty_widget.setVisible(True)
        self.no_results_widget.setVisible(False)

    def _refresh_themed_icons(self, *_args) -> None:
        """Recolour theme-tinted icon pixmaps after a light/dark switch."""
        refresh_icons()
        for i in range(self.installed_list.count()):
            item = self.installed_list.item(i)
            if item is None:
                continue
            widget = self.installed_list.itemWidget(item)
            if isinstance(widget, AppListItemWidget):
                widget.refresh_icon()
        empty_label = getattr(self, "_empty_icon_label", None)
        if empty_label is not None:
            self._empty_icon = get_icon(
                "package-x-generic", "application-x-archive", "application-x-executable"
            )
            empty_label.setPixmap(icon_pixmap(self._empty_icon, ICON_SIZE_EMPTY))
        if getattr(self, "no_results_widget", None) is not None:
            self.no_results_widget.set_icon(get_icon("system-search"), ICON_SIZE_EMPTY)

    def _sync_tray(self):
        """Create, show, or hide the persistent system tray icon based on settings."""
        if not _settings.get("tray_enabled", True):
            for tray in list(self._tray_icons):
                try:
                    tray.hide()
                except Exception:
                    pass
                tray.deleteLater()
            self._tray_icons.clear()
            if self._tray is not None:
                try:
                    self._tray.hide()
                except Exception:
                    pass
            return
        if self._tray is None:
            if not QSystemTrayIcon.isSystemTrayAvailable():
                return
            try:
                app = QApplication.instance()
                icon = QIcon()
                if isinstance(app, QApplication):
                    icon = app.windowIcon()
                if icon.isNull():
                    icon = get_icon("emblem-downloads", "system-software-update", "applications-utilities")
                self._tray = QSystemTrayIcon(icon, self)
                self._tray.setToolTip("Niruvi — AppImage Manager")
                menu = QMenu(self)
                show_action = self._add_action(menu, get_icon("go-home", "view-restore"), "Show Niruvi")
                show_action.triggered.connect(self._show_window_from_tray)
                install_action = self._add_action(menu, get_icon("list-add"), "Install AppImage...")
                install_action.triggered.connect(
                    lambda: play_and("click", lambda: self._show_window_from_tray(), self.run_install_wizard)
                )
                updates_action = self._add_action(menu, get_icon("emblem-downloads"), "Check All Apps for Updates...")
                updates_action.triggered.connect(
                    lambda: play_and("click", lambda: self._show_window_from_tray(), self._check_all_app_updates)
                )
                settings_action = self._add_action(menu, get_icon("preferences-system"), "Settings...")
                settings_action.triggered.connect(
                    lambda: play_and("click", lambda: self._show_window_from_tray(), self.open_settings)
                )
                menu.addSeparator()
                quit_action = self._add_action(menu, get_icon("application-exit"), "Quit")
                quit_action.triggered.connect(self._quit_app)
                self._tray.setContextMenu(menu)
                self._tray.activated.connect(self._on_tray_activated)
                self._tray.messageClicked.connect(self._on_tray_message_clicked)
                logger.debug("System tray icon created")
            except Exception as e:
                logger.debug("Failed to create tray icon: %s", e, exc_info=True)
                self._tray = None
                return
        self._tray.show()
        logger.debug("System tray icon shown")

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._show_window_from_tray()

    def _on_tray_message_clicked(self):
        result = getattr(self, "_last_update_result", None)
        self._show_window_from_tray()
        if result is not None:
            self._show_app_update_notification(result)
            self._last_update_result = None

    def _show_window_from_tray(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def _quit_app(self):
        play_sound("navigation")
        self._really_quit = True
        self.close()
        # Guarantee the event loop ends even if other top-level widgets
        # (or a lingering tray icon) would otherwise keep it alive.
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def closeEvent(self, event):
        if _settings.get("tray_enabled", True) and _settings.get("close_to_tray", False) and not self._really_quit:
            event.ignore()
            self.hide()
            if self._tray is not None:
                self._tray.showMessage(
                    "Niruvi",
                    "Niruvi is still running in the tray. Use 'Quit' in the tray menu to exit.",
                    QSystemTrayIcon.MessageIcon.Information,
                    3000,
                )
            return
        self._closing = True
        # Remove the tray icon so it does not linger and make the app appear
        # to still be running after Quit.
        for tray in list(self._tray_icons):
            try:
                tray.hide()
            except Exception:
                pass
            try:
                tray.deleteLater()
            except Exception:
                pass
        self._tray_icons.clear()
        if self._tray is not None:
            try:
                self._tray.hide()
            except Exception:
                pass
            try:
                self._tray.deleteLater()
            except Exception:
                pass
            self._tray = None
        self._background_updater.stop()
        if self._metadata_worker and self._metadata_worker.isRunning():
            self._metadata_worker.quit()
            self._metadata_worker.wait(1000)
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(1000)
        sound_cleanup()
        super().closeEvent(event)

    def _add_app_to_list(
        self,
        key: str,
        app_dir: str,
        version: str,
        display_name: str | None = None,
        icon_path: str | None = None,
        update_url: str = "",
        architecture: str = "",
        display_name_override: str = "",
        custom_icon_path: str = "",
        health_info: dict | None = None,
        tags: list[str] | None = None,
    ):
        if display_name is None:
            display_name = key
        version_str = version if version and version != "unknown" else ""
        tags = tags or []

        # Distinguish multiple instances with the same display name
        if key != display_name:
            label = f"{display_name}  ({key})"
        else:
            label = display_name
        text = label + (f"  (v{version_str})" if version_str else "")
        list_item = QListWidgetItem(text)
        list_item.setData(Qt.ItemDataRole.UserRole, key)
        list_item.setData(Qt.ItemDataRole.UserRole + 1, app_dir)
        list_item.setData(Qt.ItemDataRole.UserRole + 2, tags)
        tooltip = f"Path: {app_dir}\nVersion: {version_str or 'unknown'}\nName: {key}"
        if tags:
            tooltip += f"\nTags: {', '.join(tags)}"
        if health_info:
            if health_info.get("issues"):
                tooltip += f"\nIssues: {'; '.join(health_info['issues'])}"
            if health_info.get("warnings"):
                tooltip += f"\nWarnings: {'; '.join(health_info['warnings'])}"
        if not update_url:
            tooltip += "\nNo update URL configured"
        list_item.setToolTip(tooltip)
        app_size = 0
        install_time = 0.0
        size_pending = False
        if os.path.isdir(app_dir):
            try:
                install_time = os.path.getctime(app_dir)
            except OSError:
                pass
            # Sizes are computed off the main thread (ListSizeWorker) when the
            # registry has no cached value: a synchronous walk here would
            # freeze painting on every refresh.
            registry = InstallationRegistry()
            cached = registry.get(key)
            if cached and cached.size > 0:
                app_size = cached.size
            else:
                size_pending = True
        icon_qt = app_icon(icon_path, ICON_SIZE_CARD)
        widget = AppListItemWidget(
            icon=icon_qt,
            name=display_name,
            key=key,
            version=version_str,
            size=app_size,
            health_info=health_info,
        )
        self.installed_list.addItem(list_item)
        self.installed_list.setItemWidget(list_item, widget)
        list_item.setSizeHint(QSize(0, 48))
        self.installed_apps[key] = {
            "path": app_dir,
            "version": version,
            "desktop_file": find_desktop_for_app(key),
            "desktop_shortcut": find_desktop_shortcut(key),
            "display_name": display_name,
            "icon_path": icon_path,
            "size": app_size,
            "install_date": install_time,
            "update_url": update_url,
            "architecture": architecture,
            "display_name_override": display_name_override,
            "custom_icon_path": custom_icon_path,
            "health_info": health_info,
            "tags": tags,
        }
        if size_pending:
            self._pending_sizes.append((key, app_dir))

    def _start_size_probe(self):
        """Kick off (or extend) the background size sweep for uncached apps."""
        if not self._pending_sizes:
            return
        if self._size_worker is not None and self._size_worker.isRunning():
            return  # worker emits stale targets; next refresh retries ours
        targets, self._pending_sizes = self._pending_sizes, []
        worker = ListSizeWorker(targets, self)
        worker.size_ready.connect(self._on_app_size_ready)
        worker.finished.connect(lambda: setattr(self, "_size_worker", None))
        self._size_worker = worker
        worker.start()

    def _on_app_size_ready(self, key: str, size: int):
        if getattr(self, "_closing", False):
            return
        info = self.installed_apps.get(key)
        if not info or size <= 0:
            return
        info["size"] = size
        try:
            registry = InstallationRegistry()
            cached = registry.get(key) or registry.lookup_by_path(info["path"])
            if cached is not None:
                cached.size = size
                registry.add(cached)
            else:
                record = InstallationRecord(key, info["path"])
                record.size = size
                registry.add(record)
        except Exception as e:
            logger.debug("Could not cache install size for %s: %s", key, e)
        # Update the visible row without restarting the whole scan.
        for i in range(self.installed_list.count()):
            item = self.installed_list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == key:
                widget = self.installed_list.itemWidget(item)
                if widget is not None:
                    widget.set_size(size)
                break

    def scan_installed(self):
        if self._is_scanning:
            return
        self._is_scanning = True
        self.action_refresh.setEnabled(False)
        self._show_status("Scanning installed apps...")
        QApplication.processEvents()
        self.installed_apps.clear()
        self.installed_list.clear()
        install_dir = get_settings().get("install_dir", DEFAULT_INSTALL_DIR)
        seen = set()
        registry = InstallationRegistry()

        if os.path.isdir(install_dir):
            real_map: dict[str, tuple[str, bool, str]] = {}
            for item in sorted(os.listdir(install_dir)):
                app_dir = os.path.join(install_dir, item)
                if not os.path.isdir(app_dir):
                    continue
                real = os.path.realpath(app_dir)
                is_link = os.path.islink(app_dir)
                if real in real_map:
                    existing_is_link = real_map[real][1]
                    if existing_is_link and not is_link:
                        real_map[real] = (item, is_link, app_dir)
                    continue
                real_map[real] = (item, is_link, app_dir)

            for index, (item, _is_link, app_dir) in enumerate(real_map.values()):
                # Stay responsive on large installs: let the window repaint
                # without a full async refactor. User input is excluded to
                # avoid re-entrancy while the scan is in flight.
                if index and index % 20 == 0:
                    QApplication.processEvents(
                        QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
                    )
                seen.add(item)
                apprun = os.path.join(app_dir, "AppRun")
                if not os.path.isfile(apprun) or not os.access(apprun, os.X_OK):
                    continue
                try:
                    version = get_version(app_dir)
                    icon_path = self._find_app_icon(app_dir)
                    desktop_info = (
                        parse_desktop_file(os.path.join(app_dir, f"{item}.desktop"))
                        if os.path.exists(os.path.join(app_dir, f"{item}.desktop"))
                        else {}
                    )
                    desktop_info2 = {}
                    for f in os.listdir(app_dir):
                        if f.endswith(".desktop"):
                            desktop_info2 = parse_desktop_file(os.path.join(app_dir, f))
                            break
                    info = desktop_info or desktop_info2
                    display_name = info.get("Name", item) if info else item
                    rec = registry.get(item)
                    update_url = rec.update_url if rec else ""
                    arch = rec.architecture if rec else ""
                    dn_override = rec.display_name_override if rec else ""
                    cust_icon = rec.custom_icon_path if rec else ""
                    display_name = dn_override or display_name
                    health_info = check_app_health(item, app_dir, rec)
                    from niruvi.app.tags import infer_tags

                    app_tags = rec.tags if rec and rec.tags else infer_tags(display_name)
                    self._add_app_to_list(
                        item,
                        app_dir,
                        version,
                        display_name,
                        icon_path,
                        update_url,
                        arch,
                        dn_override,
                        cust_icon,
                        health_info,
                        app_tags,
                    )
                except Exception as e:
                    logging.error("Failed to scan app %s: %s", item, e)
                    continue

        stale = []
        for index, record in enumerate(registry.get_all()):
            if index and index % 20 == 0:
                QApplication.processEvents(
                    QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
                )
            if record.name in seen:
                continue
            app_dir = record.path
            if not app_dir or not os.path.isdir(app_dir):
                stale.append(record.name)
                continue
            apprun = os.path.join(app_dir, "AppRun")
            if not os.path.isfile(apprun) or not os.access(apprun, os.X_OK):
                stale.append(record.name)
                continue
            version = record.version or get_version(app_dir) or "?"
            icon_path = self._find_app_icon(app_dir)
            display_name = record.display_name_override or record.name
            cust_icon = record.custom_icon_path or icon_path or ""
            app_tags = record.tags if record.tags else infer_tags(display_name)
            self._add_app_to_list(
                record.name,
                app_dir,
                version,
                display_name,
                cust_icon or icon_path,
                record.update_url,
                record.architecture,
                record.display_name_override,
                record.custom_icon_path,
                tags=app_tags,
            )
        for name in stale:
            registry.remove(name)

        self._cleanup_mtp_orphans()
        self._start_size_probe()

        count = len(self.installed_apps)
        has_apps = count > 0
        self.installed_count_label.setText(
            f"{count} app{'s' if count != 1 else ''} installed" if has_apps else "No apps installed"
        )
        self.installed_list.setVisible(has_apps)
        self.search_edit.setVisible(has_apps)
        self.sort_combo.setVisible(has_apps)
        self.category_combo.setVisible(has_apps)
        self.empty_widget.setVisible(not has_apps)
        self.no_results_widget.setVisible(False)
        self._show_status(
            f"Found {count} installed app{'s' if count != 1 else ''}"
            if has_apps
            else "Ready — no AppImages installed yet"
        )
        self.action_refresh.setEnabled(True)
        self._is_scanning = False
        from niruvi.core.plugins import emit

        emit("scan_completed", app_count=count)

    def _filter_apps(self, text: str):
        """Filter the installed list by search text and category.

        Matching covers the fields a user is likely to try: the display name,
        the installation key, the version, and the category tags. Previously
        only the composed row text was searched, so tags and architecture were
        invisible to search.
        """
        category = self.category_combo.currentText() if hasattr(self, "category_combo") else "All Categories"
        needle = text.strip().lower()
        visible = 0
        # Remember the selection so filtering does not silently clear it.
        previously_selected = [
            item.data(Qt.ItemDataRole.UserRole)
            for item in self.installed_list.selectedItems()
            if item is not None
        ]

        for i in range(self.installed_list.count()):
            item = self.installed_list.item(i)
            if item is None:
                continue
            tags = item.data(Qt.ItemDataRole.UserRole + 2) or []
            haystack = item.text().lower()
            if needle:
                info = self.installed_apps.get(item.data(Qt.ItemDataRole.UserRole), {})
                haystack += " " + " ".join(str(t).lower() for t in tags)
                haystack += " " + str(info.get("architecture", "")).lower()
                if needle not in haystack:
                    item.setHidden(True)
                    continue
            if category == "All Categories":
                item.setHidden(False)
            elif category == "Uncategorized":
                item.setHidden(bool(tags))
            else:
                item.setHidden(category not in tags)
            if not item.isHidden():
                visible += 1

        self._sync_no_results_state(visible)
        if visible:
            self._restore_selection(previously_selected)

    def _sync_no_results_state(self, visible: int) -> None:
        """Show a 'no matches' hint instead of a mysteriously blank list."""
        has_apps = bool(self.installed_apps)
        matches = has_apps and visible == 0
        self.no_results_widget.setVisible(matches)
        self.installed_list.setVisible(has_apps and not matches)

    def _restore_selection(self, keys: list) -> None:
        if not keys:
            return
        wanted = set(keys)
        found = False
        for i in range(self.installed_list.count()):
            item = self.installed_list.item(i)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) in wanted:
                item.setSelected(True)
                found = True
        if found and self.installed_list.currentItem() is None:
            for i in range(self.installed_list.count()):
                item = self.installed_list.item(i)
                if item is not None and item.data(Qt.ItemDataRole.UserRole) in wanted:
                    self.installed_list.setCurrentItem(item)
                    break

    def _sort_apps(self, idx: int):
        self._sort_index = idx
        self._rebuild_list()

    def _rebuild_list(self):
        if not self.installed_apps:
            return
        for i in range(self.installed_list.count() - 1, -1, -1):
            item = self.installed_list.item(i)
            if item:
                w = self.installed_list.itemWidget(item)
                if w:
                    w.setVisible(False)
                    w.deleteLater()
        self.installed_list.clear()

        keys = list(self.installed_apps.keys())
        idx = self._sort_index
        if idx == 0:
            keys.sort(key=lambda k: self.installed_apps[k].get("display_name", k).lower())
        elif idx == 1:
            keys.sort(key=lambda k: self.installed_apps[k].get("size", 0), reverse=True)
        elif idx == 2:
            keys.sort(key=lambda k: self.installed_apps[k].get("install_date", 0.0), reverse=True)
        elif idx == 3:
            keys.sort(
                key=lambda k: (
                    (self.installed_apps[k].get("tags") or [""])[0].lower(),
                    self.installed_apps[k].get("display_name", k).lower(),
                )
            )

        for key in keys:
            info = self.installed_apps[key]
            self._add_app_to_list(
                key,
                info["path"],
                info["version"],
                info["display_name"],
                info["icon_path"],
                info["update_url"],
                info.get("architecture", ""),
                info.get("display_name_override", ""),
                info.get("custom_icon_path", ""),
                info.get("health_info"),
                info.get("tags"),
            )

        # Re-apply the active filter through the single code path so rebuilding
        # cannot drift from live filtering (previously this duplicated the
        # search with a narrower match set and never showed "no results").
        self._filter_apps(self.search_edit.text())

    def _cleanup_mtp_orphans(self):
        to_remove = []
        for name, info in self.installed_apps.items():
            path = info.get("path", "")
            if _is_removable_path(path):
                to_remove.append((name, path))
        if not to_remove:
            return
        names = "<br>".join(f"<b>{n}</b> — <code>{p}</code>" for n, p in to_remove)
        play_sound("warning")
        reply = QMessageBox.question(
            self,
            "Orphaned Installation",
            "Found apps installed to a phone or removable drive that is no longer connected.<br><br>"
            f"{names}<br><br>"
            "Do you want to remove them from the installed list?<br>"
            "<small>(Files on the disconnected device cannot be cleaned up until it is reconnected.)</small>",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            registry = InstallationRegistry()
            for name, _path in to_remove:
                self.installed_apps.pop(name, None)
                registry.remove(name)
            self._show_status(
                f"Removed {len(to_remove)} orphaned entr{'y' if len(to_remove) == 1 else 'ies'} from the installed list"
            )

    def _find_app_icon(self, app_dir: str) -> str | None:
        from niruvi.desktop.desktop_utils import find_icon_in_appdir, parse_desktop_file

        for f in os.listdir(app_dir):
            if f.endswith(".desktop"):
                info = parse_desktop_file(os.path.join(app_dir, f))
                icon_name = info.get("Icon")
                if icon_name:
                    path = find_icon_in_appdir(app_dir, icon_name)
                    if path:
                        return path
        common_names = {
            "icon.png",
            "icon.svg",
            "logo.png",
            "logo.svg",
            "appicon.png",
            "appicon.svg",
            "AppIcon.png",
            "AppIcon.svg",
            "application.png",
        }
        icon_exts = (".png", ".svg", ".xpm", ".ico", ".icns")
        found_by_ext: str | None = None
        for root, _, files in os.walk(app_dir):
            for f in files:
                if f in common_names:
                    return os.path.join(root, f)
                if found_by_ext is None and f.endswith(icon_exts) and not f.startswith("."):
                    found_by_ext = os.path.join(root, f)
        return found_by_ext

    def dragEnterEvent(self, event: QDragEnterEvent | None):
        if event is None:
            return
        mime = event.mimeData()
        if mime is not None and mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile() and url.path().lower().endswith(".appimage"):
                    play_sound("drop")
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dragLeaveEvent(self, event):
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent | None):
        if event is None:
            return
        urls = []
        mime = event.mimeData()
        if mime is not None:
            for url in mime.urls():
                if url.isLocalFile() and url.path().lower().endswith(".appimage"):
                    urls.append(url.path())
        if len(urls) == 1:
            self.process_appimage(urls[0])
        elif len(urls) > 1:
            self._batch_install(urls)

    def _is_already_installed(self, app_name: str, path: str) -> bool:
        if app_name in self.installed_apps:
            return True
        registry = InstallationRegistry()
        if registry.lookup_by_name(app_name):
            return True
        return bool(registry.lookup_by_path(path))

    def process_appimage(self, path: str):
        if _is_removable_path(path):
            path = _ensure_local(path, lambda m: None)

        progress = QProgressDialog("Reading AppImage metadata...", None, 0, 0, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setCancelButton(None)
        progress.show()

        result = {}

        self._metadata_worker = MetadataWorker(path)
        loop = QEventLoop()

        def on_finished(info, icon_data):
            result["info"] = info
            result["icon_data"] = icon_data
            loop.quit()

        self._metadata_worker.metadata_ready.connect(on_finished)
        start_worker(self._metadata_worker)
        loop.exec()

        progress.close()
        self._metadata_worker = None
        info = result.get("info", {"Name": Path(path).stem})
        icon_data = result.get("icon_data")

        app_name = info.get("Name", Path(path).stem)

        if self._is_already_installed(app_name, path):
            dlg = QDialog(self)
            dlg.setWindowTitle("Already Installed")
            dlg.setMinimumSize(420, 220)
            dlg.resize(480, 260)
            dlg.setModal(True)
            layout = QVBoxLayout(dlg)
            layout.setContentsMargins(20, 20, 20, 20)
            layout.setSpacing(10)

            title = QLabel(f"<b>{app_name}</b> is already installed")
            title.setWordWrap(True)
            layout.addWidget(title)
            desc = QLabel("What would you like to do?")
            layout.addWidget(desc)
            layout.addStretch()

            from PyQt6.QtWidgets import QGridLayout

            btn_grid = QGridLayout()
            btn_grid.setSpacing(8)

            btn_reinstall = QPushButton(get_icon("view-refresh"), "Re-integrate")
            btn_reinstall.clicked.connect(lambda: dlg.done(1))
            btn_grid.addWidget(btn_reinstall, 0, 0)

            btn_sbs = QPushButton(get_icon("list-add"), "Install Side-by-Side")
            btn_sbs.setToolTip("Install as a separate version alongside the existing one")
            btn_sbs.clicked.connect(lambda: dlg.done(3))
            btn_grid.addWidget(btn_sbs, 0, 1)

            btn_remove = QPushButton(get_icon("edit-delete"), "Remove && Install")
            btn_remove.clicked.connect(lambda: dlg.done(2))
            btn_grid.addWidget(btn_remove, 1, 0)

            btn_cancel = QPushButton(get_icon("dialog-cancel"), "Cancel")
            btn_cancel.clicked.connect(lambda: dlg.done(0))
            btn_grid.addWidget(btn_cancel, 1, 1)

            layout.addLayout(btn_grid)
            reply = dlg.exec()

            if reply == 0:
                return
            elif reply == 2:
                self._uninstall_app(app_name)
                app_name = f"{app_name}-reinstall"
                info = info.copy() if info else {}
                info["Name"] = app_name
            elif reply == 3:
                suffix = 2
                base_name = app_name
                while base_name in self.installed_apps:
                    base_name = f"{app_name}-{suffix}"
                    suffix += 1
                app_name = base_name
                info = info.copy() if info else {}
                info["Name"] = app_name

        play_sound("interface")
        wizard = InstallWizard(path, self, appimage_info=info, icon_data=icon_data)
        wizard.exec()
        self.scan_installed()

        if _settings.get("auto_remove_source") and app_name in self.installed_apps:
            self._cleanup_source_appimage(path, app_name)

    def _cleanup_source_appimage(self, path: str, app_name: str):
        """Delete the source AppImage after successful install if setting enabled."""
        # Skip temp downloads (from catalog) and removable-media copies
        resolved = os.path.realpath(path)
        tmpdir = tempfile.gettempdir()
        if resolved.startswith(tmpdir + "/") or "/niruvi_local_copy/" in path:
            return
        if not os.path.isfile(path):
            return
        try:
            os.unlink(path)
            self._show_status(f"Deleted source file: {Path(path).name}")
        except OSError as e:
            logging.warning("Could not delete source AppImage %s: %s", path, e)

    def _batch_install(self, paths: list[str]):
        """Install multiple AppImages sequentially."""
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

        from niruvi.core.worker import InstallQueueWorker

        play_sound("interface")
        dlg = QDialog(self)
        dlg.setWindowTitle("Batch Install")
        dlg.setMinimumSize(500, 300)
        layout = QVBoxLayout(dlg)

        layout.addWidget(QLabel(f"<b>Installing {len(paths)} AppImages</b>"))
        self._batch_status = QLabel("Starting...")
        self._batch_status.setWordWrap(True)
        layout.addWidget(self._batch_status)

        self._batch_progress = QProgressBar()
        self._batch_progress.setRange(0, len(paths))
        self._batch_progress.setValue(0)
        layout.addWidget(self._batch_progress)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(lambda: play_and("navigation", dlg.reject))
        layout.addWidget(btn_box)

        install_dir = get_settings()["install_dir"]
        worker = InstallQueueWorker()
        self._batch_worker = worker

        for p in paths:
            name = Path(p).stem
            dest = os.path.join(install_dir, name)
            worker.add_task(
                {
                    "appimage_path": p,
                    "dest_dir": dest,
                    "app_name": name,
                }
            )

        def on_task_started(app_name, idx):
            self._batch_status.setText(f"[{idx}/{len(paths)}] Installing {app_name}...")
            self._batch_progress.setValue(idx - 1)

        def on_task_finished(app_name):
            self._batch_status.setText(f"Finished: {app_name}")
            self._batch_progress.setValue(self._batch_progress.value() + 1)

        def on_task_error(msg):
            self._batch_status.setText(f"Error: {msg}")

        def on_task_log(msg):
            self._batch_status.setText(f"[{worker.task_count()}] {msg}")

        def on_all_finished():
            self._batch_status.setText("All installations complete!")
            self.scan_installed()

        worker.task_started.connect(on_task_started)
        worker.task_finished.connect(on_task_finished)
        worker.task_error.connect(on_task_error)
        worker.task_log.connect(on_task_log)
        worker.all_finished.connect(on_all_finished)

        dlg.finished.connect(lambda: worker.stop() if worker.isRunning() else None)
        start_worker(worker)
        dlg.exec()
        if worker.isRunning():
            worker.stop()
        if not worker.wait(5000):
            worker.all_finished.connect(self._release_batch_worker)
            return
        self._batch_worker = None

    def _release_batch_worker(self):
        self._batch_worker = None

    def run_install_wizard(self, file_path=None):
        if not file_path:
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                "Select AppImage file",
                os.path.expanduser("~"),
                "AppImage files (*.AppImage);;All files (*)",
            )
            if not file_path:
                return
        self.process_appimage(file_path)

    def _build_app_context_menu(self, app_name: str) -> QMenu | None:
        """Build the per-app context menu.

        Actions carry a stable string key in their data() so dispatch does not
        depend on identity comparisons. Returns None if the app is unknown.
        """
        app_info = self.installed_apps.get(app_name)
        if app_info is None:
            return None
        is_self = app_name == __app_name__

        def add(menu_ref: QMenu, icon, text: str, key: str):
            act = menu_ref.addAction(icon, text)
            act.setData(key)
            return act

        menu = QMenu(self)
        add(menu, get_icon("help-about", "dialog-information"), "App Info", "info")
        menu.addSeparator()

        if not is_self:
            add(menu, get_icon("media-playback-start"), "Run", "run")
            add(menu, get_icon("security-low", "document-open-recent"), "Run Without Sandbox", "run_unsandboxed")
            menu.addSeparator()

        manage_menu = menu.addMenu(get_icon("preferences-system", "emblem-system"), "Manage")
        add(manage_menu, get_icon("emblem-downloads"), "Update…", "update")
        if bool(app_info.get("update_url")):
            add(manage_menu, get_icon("network-server", "emblem-downloads"), "Check for Updates", "check_update")
        add(manage_menu, get_icon("dialog-password", "security-high"), "Verify Installation", "verify")
        add(manage_menu, get_icon("emblem-system", "preferences-system"), "Repair Installation", "repair")
        manage_menu.addSeparator()
        add(
            manage_menu,
            get_icon("text-x-script", "applications-system", "utilities-terminal", "folder-open"),
            "Edit Hooks for This App…",
            "hooks",
        )

        desktop_menu = menu.addMenu(get_icon("user-desktop"), "Desktop")
        add(desktop_menu, get_icon("folder-open"), "Open Folder", "open_folder")
        has_shortcut = bool(app_info.get("desktop_shortcut"))
        shortcut_text = "Remove Desktop Shortcut" if has_shortcut else "Create Desktop Shortcut"
        add(desktop_menu, get_icon("user-desktop"), shortcut_text, "shortcut")

        menu.addSeparator()
        add(menu, get_icon("edit-delete"), "Uninstall…", "uninstall")
        return menu

    def _dispatch_context_action(self, app_name: str, key):
        if key == "info":
            self._show_app_info(app_name)
        elif key == "run":
            self._run_app(app_name)
        elif key == "run_unsandboxed":
            self._run_app(app_name, unsandboxed=True)
        elif key == "update":
            self._update_app(app_name)
        elif key == "check_update":
            app_info = self.installed_apps.get(app_name, {})
            self._check_single_app_update(app_name, app_info.get("update_url", ""), app_info.get("version", ""))
        elif key == "uninstall":
            self._uninstall_app(app_name)
        elif key == "open_folder":
            self._open_folder(app_name)
        elif key == "shortcut":
            if self.installed_apps.get(app_name, {}).get("desktop_shortcut"):
                self._remove_desktop_shortcut(app_name)
            else:
                self._create_desktop_shortcut(app_name)
        elif key == "verify":
            self._verify_app(app_name)
        elif key == "repair":
            self._repair_app(app_name)
        elif key == "hooks":
            self._open_app_hooks(app_name)

    def _show_context_menu(self, pos):
        item = self.installed_list.itemAt(pos)
        if not item:
            return
        app_name = item.data(Qt.ItemDataRole.UserRole)
        if app_name not in self.installed_apps:
            return

        menu = self._build_app_context_menu(app_name)
        if menu is None:
            return
        action = menu.exec(self.installed_list.mapToGlobal(pos))
        if action is None:
            return
        self._dispatch_context_action(app_name, action.data())

    def _on_app_double_clicked(self, item):
        app_name = item.data(Qt.ItemDataRole.UserRole)
        if app_name and app_name != __app_name__:
            play_sound("click")
            self._run_app(app_name)

    def _show_launch_refused(self, app_name: str, refusals: list[str], reason: str = ""):
        """Fail-closed: no app ran. Explain exactly why instead of a quiet
        unsandboxed fallback."""
        play_sound("error")
        lines = []
        if reason:
            lines.append(f"<b>Intentional dispatch reason:</b> {reason}")
        for r in refusals:
            lines.append("• " + r)
        try:
            from niruvi.core.sandbox_audit import read_launches

            recent = read_launches(limit=5)
            if recent:
                entry = recent[0]
                lines.append(
                    f"<br><small>Logged to the audit log at {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())}"
                    f" (result: {entry.get('result')})</small>"
                )
        except Exception:
            logger.debug("audit read for refusal dialog failed", exc_info=True)
        dlg = QMessageBox(self)
        dlg.setIcon(QMessageBox.Icon.Critical)
        dlg.setWindowTitle("Launch refused")
        dlg.setText(f"<b>{app_name}</b> was not started.")
        dlg.setInformativeText(
            "Sandbox enforcement could not be established. Reasons:\n\n"
            + "\n".join(lines)
        )
        dlg.setStandardButtons(QMessageBox.StandardButton.Ok)
        dlg.exec()

    def _show_run_error(self, app_name: str, app_dir: str, detail: str):
        play_sound("error")
        dlg = QDialog(self)
        dlg.setWindowTitle("Failed to Run")
        dlg.setMinimumWidth(580)
        dlg.setMinimumHeight(360)
        dlg.setModal(True)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(12)

        title = QLabel(f"<h3>Could not run <b>{app_name}</b></h3>")
        title.setWordWrap(True)
        layout.addWidget(title)

        # Specific error details
        detail_lbl = QLabel(detail if detail else "Unknown error")
        detail_lbl.setWordWrap(True)
        detail_lbl.setStyleSheet(
            style(
                "background: {colors.surface}; border: 1px solid {colors.border}; "
                "border-radius: 4px; padding: {spacing.lg}; font-family: monospace; font-size: 11px;"
            )
        )
        layout.addWidget(detail_lbl)

        # Run diagnostics inline
        from niruvi.app.health_check import check_app_runnable, check_fuse_available

        fuse = check_fuse_available()
        diag = check_app_runnable(app_name, app_dir)

        sys_lines = []
        sys_lines.append(f"<b>FUSE available:</b> {'Yes' if fuse else 'No'}")
        if diag.get("info", {}).get("missing_libs"):
            ml = diag["info"]["missing_libs"]
            sys_lines.append(f"<b>Missing libraries:</b> {', '.join(ml[:5])}")
        if diag.get("info", {}).get("missing_libs"):
            from niruvi.app.health_check import suggest_lib_fixes

            fixes = suggest_lib_fixes(diag["info"]["missing_libs"])
            if fixes:
                sys_lines.append(
                    "<b>Install packages:</b> <code style='font-family:monospace;'>" + fixes[0] + "</code>"
                )
        if diag.get("info", {}).get("interpreter"):
            sys_lines.append(f"<b>Interpreter:</b> {diag['info']['interpreter']}")
        if diag["warnings"]:
            for w in diag["warnings"][:3]:
                sys_lines.append(f"<b>Warning:</b> {w}")

        if sys_lines:
            info_lbl = QLabel("<br>".join(sys_lines))
            info_lbl.setWordWrap(True)
            info_lbl.setStyleSheet("font-size: 11px;")
            layout.addWidget(info_lbl)

        suggestions = _get_app_suggestions(app_name)
        if suggestions:
            s_text = "<b>Possible fixes:</b><br>"
            for label, flag in suggestions:
                s_text += f"• {label}: <code>{flag}</code><br>"
            s_text += (
                "<br><span style='font-size:0.9em;'>"
                "Try adding these flags in the app's <b>Run Arguments</b> "
                "under Properties, or reinstall with different options.</span>"
            )
            s_lbl = QLabel(s_text)
            s_lbl.setWordWrap(True)
            layout.addWidget(s_lbl)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        diagnose_btn = QPushButton(get_icon("emblem-important"), "Run Full Diagnostics")
        diagnose_btn.clicked.connect(
            lambda: play_and("click", dlg.accept, lambda: self._show_app_diagnostics(app_name))
        )
        btn_layout.addWidget(diagnose_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        dlg.exec()

    def _run_app(self, app_name: str, unsandboxed: bool = False):
        play_sound("click")
        app_info = self.installed_apps[app_name]
        app_dir = app_info["path"]
        registry = InstallationRegistry()
        record = registry.get(app_name)
        # Only trusted per-app overrides are passed here — Shield._build_env()
        # filters the host environment through its security allowlist.
        env = dict(record.env_vars) if record and record.env_vars else {}

        # Browsers ship their own sandbox (setuid/namespaces) — nesting it
        # inside Niruvi Shield crashes at startup, so skip our sandbox for
        # them. This is a PER-LAUNCH decision only: the stored sandbox config
        # is never modified, so a malicious app cannot permanently disable
        # its own isolation by naming itself "Chrome-whatever".
        low = app_name.lower()
        is_browser = any(x in low for x in ("chrome", "chromium", "brave", "edge", "firefox"))
        if is_browser:
            logger.info(
                "Browser detected (%s): launching without Niruvi sandbox to avoid nested-sandbox crash", app_name
            )

        apprun = os.path.join(app_dir, "AppRun")
        if not os.path.isfile(apprun):
            expected = os.path.join(get_settings()["install_dir"], app_name, "AppRun")
            if os.path.isfile(expected):
                apprun = expected
                app_dir = os.path.dirname(expected)
            else:
                self._run_app_fallback(app_name, app_dir, env, record)
                return
        self._apply_portable_env(env, app_dir, record)
        # Always launch with the FULL host environment (HOME, PATH, XDG_*, …).
        # Without this, non-sandboxed apps inherit only the tiny per-app override
        # dict (often empty) → HOME="" → apps do `mkdir $HOME/.config` in `/`.
        launch_env = self._full_env(env)

        # ── Pre-flight diagnostics ──
        from niruvi.app.health_check import check_app_runnable, check_fuse_available

        diag = check_app_runnable(app_name, app_dir)
        if not diag["healthy"]:
            diag_issues = list(diag["issues"])
            diag_warnings = list(diag["warnings"])
            fuse_ok = check_fuse_available()
            if not fuse_ok and not diag.get("info", {}).get("missing_libs"):
                self._run_app_fallback(app_name, app_dir, launch_env, record)
                return
            if not fuse_ok:
                diag_issues.append("FUSE is not available — app may not run")
            info_lines = []
            if diag.get("info", {}).get("interpreter"):
                info_lines.append(f"Interpreter: {diag['info']['interpreter']}")
            if diag.get("info", {}).get("missing_libs"):
                info_lines.append("Missing libraries: " + ", ".join(diag["info"]["missing_libs"][:5]))
            if diag_warnings:
                for w in diag_warnings[:3]:
                    info_lines.append(f"Warning: {w}")
            detail = "\n".join(diag_issues)
            if info_lines:
                detail += "\n\n" + "\n".join(info_lines)
            self._show_run_error(app_name, app_dir, detail)
            if not fuse_ok:
                self._run_app_fallback(app_name, app_dir, launch_env, record)
            return

        unsandboxed = unsandboxed or is_browser
        hook_results = run_hooks(app_name, app_dir, launch_env)
        for hr in hook_results:
            if hr["returncode"] != 0:
                logging.warning("Hook %s failed: %s", hr["hook"], hr["stderr"])
        cmd = [apprun]
        if record and record.run_args:
            import shlex

            cmd.extend(shlex.split(record.run_args))
        # Fail-closed launch: SandboxManager decides sandboxing, never a
        # quiet unsandboxed here. Refusals surface in a dialog.
        from niruvi.core.sandbox_manager import get_sandbox_manager, PolicyResolutionError

        if unsandboxed and "--unsandboxed" not in sys.argv:
            # Browser ships its own sandbox. Keep isolation intent visible:
            # the exclusion is explicit, recorded in the audit log and shown
            # in the status bar - it is never a silent skip. The stored
            # per-app sandbox policy itself is not modified.
            override_reason = f"browser sandbox: {app_name} runs its own process sandbox; Niruvi's sandbox would nest against it"
        elif "--unsandboxed" in sys.argv:
            override_reason = "explicit --unsandboxed CLI override"
        else:
            override_reason = ""

        sandbox = get_sandbox_manager()
        try:
            policy = sandbox.resolve_policy(app_name, record)
        except PolicyResolutionError as e:
            self._show_launch_refused(app_name, [f"Policy resolution failed: {e}"])
            return
        if not policy.enabled:
            override_reason = override_reason or f"sandbox disabled in saved policy ({policy.profile})"
        result = sandbox.launch(
            app_name,
            app_dir,
            cmd,
            policy,
            record=record,
            env_overrides=env,
            executable=cmd[0] if cmd else "",
            cwd=app_dir,
            override_reason=override_reason,
        )
        if not result.ok:
            self._show_launch_refused(app_name, result.refusals, reason=override_reason)
            return
        for w in result.warnings:
            logger.info("Launch warning (%s): %s", app_name, w)
        _track_detached(result.proc)
        self._current_launch = result
        self._current_launch_pid = result.pid
        self._current_launch_app = app_name
        if result.sandboxed:
            self._show_status(f"Running {app_name} (sandboxed, profile {result.profile}, {result.backend})")
        else:
            self._show_status(f"Running {app_name} (unsandboxed - {override_reason or 'user choice'})")
        from niruvi.core.plugins import emit

        emit("app_launched", app_name=app_name, app_dir=app_dir)
        # Post-launch quick health check: wait briefly, detect immediate crash
        self._monitor_launch(app_name, result.proc)

    def _monitor_launch(self, app_name: str, proc: subprocess.Popen):
        """Check if process crashes immediately after launch (main-thread safe)."""

        def _on_timeout():
            if getattr(self, "_closing", False):
                return
            try:
                proc.wait(timeout=0)
                if proc.returncode != 0 and proc.returncode is not None:
                    from PyQt6.QtWidgets import QMessageBox

                    play_sound("warning")
                    QMessageBox.warning(
                        self,
                        f"{app_name} exited unexpectedly",
                        f"<b>{app_name}</b> exited quickly (code {proc.returncode}).<br><br>"
                        "This typically means:<br>"
                        "• Missing system libraries<br>"
                        "• Incompatible architecture<br>"
                        "• Corrupted AppImage<br><br>"
                        "Try reinstalling the app or check its App Info for diagnostics.",
                    )
            except subprocess.TimeoutExpired:
                pass

        QTimer.singleShot(3000, _on_timeout)

    def _run_app_fallback(self, app_name: str, app_dir: str, env: dict | None = None, record=None):
        """FUSE-less launch path (extract-and-run). Still goes through the
        SandboxManager so the sandbox choice is never silently lost."""
        from niruvi.core.sandbox_manager import get_sandbox_manager, PolicyResolutionError

        appimage_path = self._find_appimage_in_dir(app_dir)
        extract_dir = None
        if appimage_path and os.path.isfile(appimage_path):
            extract_dir = self._extract_target(appimage_path)
            cmd = [appimage_path, "--appimage-extract-and-run"]
        else:
            ret = self._try_extract_and_run_temp(app_dir, app_name, env, record)
            if ret:
                return
            self._show_run_error(app_name, app_dir, "All launch methods failed (FUSE, namespace, extraction).")
            return

        sandbox = get_sandbox_manager()
        try:
            policy = sandbox.resolve_policy(app_name, record)
        except PolicyResolutionError as e:
            self._show_launch_refused(app_name, [f"Policy resolution failed: {e}"])
            return
        result = sandbox.launch(
            app_name,
            extract_dir or app_dir,
            cmd,
            policy,
            record=record,
            env_overrides=env,
            executable=cmd[0] if cmd else "",
            cwd=extract_dir or app_dir,
            override_reason=self._fallback_reason(app_name),
        )
        if not result.ok:
            self._show_launch_refused(app_name, result.refusals, reason=self._fallback_reason(app_name))
            return
        _track_detached(result.proc)
        self._current_launch = result
        self._current_launch_pid = result.pid
        self._current_launch_app = app_name
        self._show_status(f"Running {app_name} (namespace mode, profile {result.profile})")

    _FALLBACK_REASON = (
        "FUSE unavailable: launched with --appimage-extract-and-run; the sandbox "
        "still applies, but the extraction directory replaces the mounted one."
    )

    def _fallback_reason(self, app_name: str) -> str:
        return self._FALLBACK_REASON

    def _extract_target(self, appimage_path: str) -> str | None:
        # For extract-and-run, the working directory is where the AppImage lives.
        return os.path.dirname(appimage_path) or None

    def _find_appimage_in_dir(self, app_dir: str) -> str | None:
        for f in os.listdir(app_dir):
            if f.endswith(".AppImage") and os.path.isfile(os.path.join(app_dir, f)):
                return os.path.join(app_dir, f)
        parent = os.path.dirname(app_dir)
        for f in os.listdir(parent):
            if f.lower().startswith(app_dir.lower().replace(" ", "")) and f.endswith(".AppImage"):
                return os.path.join(parent, f)
        return None

    def _apply_portable_env(self, env: dict, app_dir: str, record) -> dict:
        if record:
            sc = record.sandbox_config or {}
            if sc.get("portable_home", False) or sc.get("portable", False):
                env["HOME"] = os.path.join(app_dir, ".home")
            if sc.get("portable_config", False):
                env["XDG_CONFIG_HOME"] = os.path.join(app_dir, ".config")
        return env

    def _full_env(self, env: dict | None) -> dict:
        """Merge trusted overrides over a copy of the full host environment.

        Used by direct (non-Shield) launch paths that Popen without going
        through Shield._build_env(), which would otherwise receive only the
        small per-app override dict.
        """
        full = os.environ.copy()
        if env:
            full.update(env)
        return full

    def _try_extract_and_run(self, appimage_path: str, app_name: str, env: dict | None = None, record=None) -> bool:
        try:
            from niruvi.app.health_check import check_namespace_available

            use_namespace = check_namespace_available()
            cmd = [appimage_path, "--appimage-extract-and-run"]
            if record and record.run_args:
                import shlex

                cmd.extend(shlex.split(record.run_args))
            if use_namespace:
                runner = ["unshare", "--user", "--mount"]
                runner.extend(cmd)
                cmd = runner
            p = subprocess.Popen(
                cmd,
                env=self._apply_portable_env(self._full_env(env), os.path.dirname(appimage_path), record),
                start_new_session=True,
            )
            _track_detached(p)
            self._show_status(f"Running {app_name} (namespace mode)")
            return True
        except OSError:
            return False

    _temp_launch_dirs: list[str] = []

    def _try_extract_and_run_temp(self, app_dir: str, app_name: str, env: dict | None = None, record=None) -> bool:
        import atexit
        import shutil
        import tempfile

        tmp = tempfile.mkdtemp(prefix=f"niruvi-{app_name}-")
        try:
            appimage_path = self._find_appimage_in_dir(app_dir)
            if appimage_path and os.path.isfile(appimage_path):
                from niruvi.core.worker import extract_appimage_sync

                extract_appimage_sync(appimage_path, tmp)
                apprun = os.path.join(tmp, "AppRun")
                if os.path.isfile(apprun):
                    cmd = [apprun]
                    if record and record.run_args:
                        import shlex

                        cmd.extend(shlex.split(record.run_args))
                    p = subprocess.Popen(
                        cmd,
                        cwd=tmp,
                        env=self._apply_portable_env(self._full_env(env), app_dir, record),
                        start_new_session=True,
                    )
                    _track_detached(p)
                    self._temp_launch_dirs.append(tmp)
                    if not hasattr(self, "_temp_atexit_registered"):
                        atexit.register(self._cleanup_temp_dirs)
                        self._temp_atexit_registered = True
                    self._show_status(f"Running {app_name} (extracted mode)")
                    return True
        except Exception as e:
            logging.error("Temp extraction failed for %s: %s", app_name, e)
        shutil.rmtree(tmp, ignore_errors=True)
        return False

    def _cleanup_temp_dirs(self):
        import shutil

        for d in list(self._temp_launch_dirs):
            shutil.rmtree(d, ignore_errors=True)
        self._temp_launch_dirs.clear()

    def _uninstall_app(self, app_name: str):
        app_info = self.installed_apps.get(app_name)
        if not app_info:
            return
        app_dir = app_info.get("path", "")
        if not app_dir or not os.path.isdir(app_dir):
            expected = os.path.join(get_settings()["install_dir"], app_name)
            if os.path.isdir(expected):
                app_dir = expected
            else:
                play_sound("warning")
                QMessageBox.warning(self, "Cannot Uninstall", f"The install directory for {app_name} was not found.")
                return

        # Safety: refuse to remove system directories
        SAFE_PREFIXES = (
            os.path.expanduser("~/Applications"),
            os.path.expanduser("~/.local"),
        )
        real = os.path.realpath(app_dir)
        if not any(real.startswith(p) for p in SAFE_PREFIXES):
            play_sound("error")
            QMessageBox.critical(
                self,
                "Security Error",
                f"Refusing to uninstall: the path '{real}' is not in a managed directory.\n\n"
                f"Uninstall is only allowed for paths under ~/Applications or ~/.local.",
            )
            return

        # Revoke all permissions for this app on uninstall
        try:
            _get_perm_store().clear(app_name)
        except Exception:
            pass

        play_sound("interface")
        wizard = UninstallWizard(app_name, app_dir, self)
        if wizard.exec() == QDialog.DialogCode.Accepted:
            self.scan_installed()
            self._show_status(f"Uninstalled {app_name}")
            from niruvi.core.plugins import emit

            emit("app_removed", app_name=app_name, app_dir=app_dir)

    def _update_app(self, app_name: str):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            f"Select new AppImage for {app_name}",
            os.path.expanduser("~"),
            "AppImage files (*.AppImage);;All files (*)",
        )
        if not file_path:
            return
        play_sound("click")
        old_dir = self.installed_apps[app_name]["path"]

        # Create a single previous-version backup for rollback.  A second
        # ``.backup`` copy is intentionally NOT made: it duplicated the whole
        # install (and the temp extraction) on every update.
        prev_dir = old_dir + ".prev"
        if os.path.exists(prev_dir):
            shutil.rmtree(prev_dir, ignore_errors=True)
        if os.path.isdir(old_dir) and not self._create_prev_backup(old_dir, prev_dir):
            play_sound("error")
            QMessageBox.critical(
                self,
                "Backup Failed",
                f"Could not back up {app_name} before updating.\n"
                "The update was cancelled so the existing installation is kept.",
            )
            return

        backup_dir = None

        dest_dir = old_dir

        progress = QProgressDialog(f"Updating {app_name}...", "Cancel", 0, 0, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setCancelButton(None)
        progress.show()

        self.worker = ExtractionWorker(file_path, dest_dir, app_name, self)
        self.worker.extraction_finished.connect(
            lambda d, n: self._on_update_finished(d, n, progress, backup_dir, prev_dir, source_path=file_path)
        )
        self.worker.extraction_error.connect(
            lambda e: self._on_update_error(e, progress, backup_dir, prev_dir, dest_dir)
        )
        start_worker(self.worker)

    def _create_prev_backup(self, app_dir: str, prev_dir: str) -> bool:
        """Create a .prev backup of the current version for rollback.

        Returns True when the backup is in place.  Callers use this as the
        *single* rollback copy (the older code also made a second ``.backup``
        copy, doubling peak disk usage during every update).
        """
        try:
            shutil.copytree(app_dir, prev_dir, dirs_exist_ok=True, symlinks=True)
            # Save version info
            meta = {}
            meta_path = os.path.join(app_dir, ".appimage-manager.json")
            if os.path.exists(meta_path):
                with open(meta_path) as f:
                    meta = json.load(f)
            meta["rollback_date"] = str(Path(prev_dir).stat().st_ctime)
            prev_meta = os.path.join(prev_dir, ".appimage-manager.json")
            with open(prev_meta, "w") as f:
                json.dump(meta, f)
            return True
        except OSError as e:
            logging.warning("Could not create rollback backup: %s", e)
            return False

    def _revert_app(self, app_name: str):
        """Revert to the .prev version if it exists."""
        app_info = self.installed_apps.get(app_name)
        if not app_info:
            return
        app_dir = app_info["path"]
        prev_dir = app_dir + ".prev"
        if not os.path.isdir(prev_dir):
            play_sound("info")
            QMessageBox.information(self, "No Backup", f"No previous version found for {app_name}.")
            return
        play_sound("warning")
        reply = QMessageBox.question(
            self,
            "Revert Version",
            f"Revert {app_name} to the previous version?\nThe current version will be moved to a backup.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            # Move current to temp, prev to current, then remove temp
            temp_dir = app_dir + ".tmp_revert"
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
            os.rename(app_dir, temp_dir)
            os.rename(prev_dir, app_dir)
            shutil.rmtree(temp_dir, ignore_errors=True)
            play_sound("success")
            self.scan_installed()
            self._show_status(f"Reverted {app_name} to previous version")
        except OSError as e:
            play_sound("error")
            QMessageBox.critical(self, "Revert Failed", str(e))

    def _on_update_finished(
        self,
        dest_dir: str,
        app_name: str,
        progress: QProgressDialog,
        backup_dir: str | None,
        prev_dir: str | None = None,
        source_path: str = "",
    ):
        progress.close()
        if prev_dir and os.path.isdir(prev_dir):
            # Keep .prev for rollback
            pass
        if backup_dir and os.path.isdir(backup_dir):
            try:
                shutil.rmtree(backup_dir)
            except OSError:
                pass
        if source_path and get_settings().get("delta_updates", True) and os.path.isfile(source_path):
            try:
                seed_target = os.path.join(dest_dir, f"{app_name}.AppImage")
                shutil.copy2(source_path, seed_target)
                os.chmod(seed_target, 0o755)
            except OSError as e:
                self._show_status(f"Warning: could not keep delta seed: {e}")
        version = get_version(dest_dir)
        metadata = {
            "version": version,
            "install_date": str(Path(dest_dir).stat().st_ctime),
        }
        meta_path = os.path.join(dest_dir, ".appimage-manager.json")
        with tempfile.NamedTemporaryFile(mode="w", dir=os.path.dirname(meta_path), delete=False, suffix=".tmp") as tf:
            json.dump(metadata, tf)
            tmp_path = tf.name
        os.replace(tmp_path, meta_path)

        if get_settings().get("create_desktop", True):
            try:
                create_desktop_entry(dest_dir, app_name, self)
            except Exception as e:
                self._show_status(f"Warning: could not create desktop entry: {e}")

        if get_settings().get("register_mime_handler", True):
            try:
                register_mime_handler(app_name)
            except Exception as e:
                logger.debug("MIME registration failed for %s: %s", app_name, e, exc_info=True)

        registry = InstallationRegistry()
        record = registry.get(app_name)
        old_version = record.version if record else "unknown"
        if record:
            record.version = version
            registry.add(record)

        self.scan_installed()
        self._show_status(f"Updated {app_name}")
        play_sound("success")
        from niruvi.core.plugins import emit

        emit("app_updated", app_name=app_name, old_version=old_version, new_version=version)

    def _on_update_error(
        self, error_msg: str, progress: QProgressDialog, backup_dir: str | None, prev_dir: str, dest_dir: str
    ):
        play_sound("error")
        progress.close()
        restored = False
        if backup_dir and os.path.isdir(backup_dir):
            try:
                if os.path.exists(dest_dir):
                    shutil.rmtree(dest_dir)
                shutil.copytree(backup_dir, dest_dir, dirs_exist_ok=True)
                shutil.rmtree(backup_dir)
                restored = True
            except OSError as e:
                play_sound("error")
                QMessageBox.critical(
                    self,
                    "Restore Failed",
                    f"Update failed and backup could not be restored: {e}\n\nYour data is at: {backup_dir}",
                )
                return
        elif prev_dir and os.path.isdir(prev_dir):
            try:
                if os.path.exists(dest_dir):
                    shutil.rmtree(dest_dir)
                shutil.copytree(prev_dir, dest_dir, dirs_exist_ok=True)
                restored = True
            except OSError as e:
                play_sound("error")
                QMessageBox.critical(
                    self,
                    "Restore Failed",
                    f"Update failed and rollback copy could not be restored: {e}\n\nYour data is at: {prev_dir}",
                )
                return
        play_sound("error")
        if restored:
            QMessageBox.critical(self, "Update Error", f"Failed to update: {error_msg}\n\nPrevious version restored.")
        else:
            QMessageBox.critical(self, "Update Error", f"Failed to update: {error_msg}")

    def _create_desktop_shortcut(self, app_name: str):
        app_info = self.installed_apps[app_name]
        apprun = os.path.join(app_info["path"], "AppRun")
        icon_path = app_info.get("icon_path")
        shortcut_path = create_desktop_shortcut(app_name, apprun, icon_path)
        if shortcut_path:
            play_sound("success")
            self._show_status(f"Desktop shortcut created for {app_name}")
            app_info["desktop_shortcut"] = shortcut_path
        else:
            play_sound("warning")
            QMessageBox.warning(self, "Error", "Failed to create desktop shortcut.")

    def _remove_desktop_shortcut(self, app_name: str):
        shortcut = find_desktop_shortcut(app_name)
        if shortcut and os.path.exists(shortcut):
            play_sound("click")
            os.remove(shortcut)
            self.installed_apps[app_name]["desktop_shortcut"] = None
            self._show_status(f"Desktop shortcut removed for {app_name}")

    def _shortcut_uninstall(self):
        item = self.installed_list.currentItem()
        if item:
            app_name = item.data(Qt.ItemDataRole.UserRole)
            if app_name:
                self._uninstall_app(app_name)

    def _shortcut_app_info(self):
        item = self.installed_list.currentItem()
        if item:
            app_name = item.data(Qt.ItemDataRole.UserRole)
            if app_name:
                self._show_app_info(app_name)

    def _shortcut_open_folder(self):
        item = self.installed_list.currentItem()
        if item:
            app_name = item.data(Qt.ItemDataRole.UserRole)
            if app_name:
                self._open_folder(app_name)

    def _open_folder(self, app_name: str):
        app_dir = self.installed_apps[app_name]["path"]
        play_sound("click")
        try:
            p = subprocess.Popen(["xdg-open", app_dir], start_new_session=True)
            _track_detached(p)
        except OSError:
            play_sound("error")
            QMessageBox.critical(self, "Error", "Could not open folder.")

    def _verify_app(self, app_name: str):
        app_info = self.installed_apps.get(app_name)
        if not app_info:
            play_sound("error")
            return
        play_sound("click")
        app_dir = app_info["path"]
        if not app_dir or not os.path.isdir(app_dir):
            play_sound("error")
            QMessageBox.critical(
                self, "Verification Failed", f"<b>{app_name}</b> directory not found:<br><br>{app_dir}"
            )
            return
        try:
            results = verify_complete(app_dir)
        except Exception as e:
            play_sound("error")
            logger.exception("Verification failed for %s: %s", app_name, e)
            QMessageBox.critical(self, "Verification Error", f"Failed to verify <b>{app_name}</b>:<br><br>{e}")
            return
        passed = sum(1 for r in results if r.passed)
        total = len(results)
        details = "\n".join(f"[{'PASS' if r.passed else 'FAIL'}] {r.details}" for r in results)
        if all(results):
            play_sound("notification")
            QMessageBox.information(
                self, "Verification Passed", f"<b>{app_name}</b> passed all {total} checks.<br><br><pre>{details}</pre>"
            )
        else:
            play_sound("warning")
            QMessageBox.warning(
                self,
                "Verification Failed",
                f"<b>{app_name}</b> failed {total - passed}/{total} checks.<br><br><pre>{details}</pre>",
            )

    def _repair_app(self, app_name: str):
        app_info = self.installed_apps.get(app_name)
        if not app_info:
            play_sound("error")
            return
        play_sound("click")
        app_dir = app_info["path"]
        if not app_dir or not os.path.isdir(app_dir):
            play_sound("error")
            QMessageBox.critical(self, "Repair Failed", f"<b>{app_name}</b> directory not found:<br><br>{app_dir}")
            return
        try:
            report = repair_full(app_name, app_dir)
        except Exception as e:
            play_sound("error")
            logger.exception("Repair failed for %s: %s", app_name, e)
            QMessageBox.critical(self, "Repair Error", f"Failed to repair <b>{app_name}</b>:<br><br>{e}")
            return
        if report.all_succeeded:
            play_sound("notification")
            QMessageBox.information(
                self,
                "Repair Complete",
                f"<b>{app_name}</b> has been repaired.<br><br>"
                f"{report.summary()}<br><br>"
                "Desktop entries, icons, registry, and permissions restored.",
            )
        else:
            play_sound("warning")
            QMessageBox.warning(
                self,
                "Repair Partial",
                f"<b>{app_name}</b> repair completed with issues.<br><br>"
                f"{report.summary()}<br><br>"
                "Some items could not be fixed. Try reinstalling the application.",
            )
        self.scan_installed()

    def _open_app_hooks(self, app_name: str):
        hooks_dir = ensure_hooks_dir(app_name)
        play_sound("click")
        try:
            p = subprocess.Popen(["xdg-open", hooks_dir], start_new_session=True)
            _track_detached(p)
        except OSError:
            play_sound("error")
            QMessageBox.critical(self, "Error", "Could not open hooks directory.")

    def _show_app_diagnostics(self, app_name: str):
        app_info = self.installed_apps.get(app_name)
        if not app_info:
            return
        app_dir = app_info["path"]
        from niruvi.app.health_check import check_app_runnable, check_fuse_available
        from niruvi.desktop.installation_registry import InstallationRegistry

        registry = InstallationRegistry()
        record = registry.get(app_name)

        health = check_app_health(app_name, app_dir, record)
        runnable = check_app_runnable(app_name, app_dir)
        fuse = check_fuse_available()

        has_real_issues = bool(health["issues"]) or bool(runnable["issues"])

        # Design tokens for error/warning cards
        error_bg = design_color("error")
        error_border = design_color("error")
        warn_bg = design_color("warning")
        warn_border = design_color("warning")
        ok_color = design_color("success")
        disabled_hex = design_color("subtle")
        mid_hex = design_color("muted")

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Diagnostics: {app_name}")
        dlg.setMinimumWidth(520)
        dlg.setMinimumHeight(380)
        dlg.setModal(True)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(8)

        header = QLabel(f"<h3>Diagnostics for <b>{app_name}</b></h3>")
        layout.addWidget(header)

        if has_real_issues:
            icon_lbl = QLabel()
            icon_lbl.setPixmap(icon_pixmap(get_icon("emblem-important", "dialog-warning"), 32))
            icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(icon_lbl)

        if runnable["issues"]:
            lbl = QLabel(
                f"<b style='color:{COLOR_ERROR};'>Pre-launch issues:</b><br>"
                + "<br>".join(f"• {i}" for i in runnable["issues"])
            )
            lbl.setWordWrap(True)
            lbl.setStyleSheet(f"background:{error_bg};border:1px solid {error_border};border-radius:4px;padding:8px;")
            layout.addWidget(lbl)

        if runnable["issues"]:
            lbl = QLabel(
                f"<b style='color:{COLOR_INK};'>Pre-launch issues:</b><br>"
                + "<br>".join(f"• {i}" for i in runnable["issues"])
            )
            lbl.setWordWrap(True)
            lbl.setStyleSheet(f"background:{error_bg};border:1px solid {error_border};border-radius:4px;padding:8px;")
            layout.addWidget(lbl)

        info_lines = []
        info_lines.append(f"<b>FUSE:</b> {'Available' if fuse else 'Not available'}")
        if runnable.get("info", {}).get("interpreter"):
            info_lines.append(f"<b>Interpreter:</b> {runnable['info']['interpreter']}")
        if runnable.get("info", {}).get("missing_libs"):
            ml = runnable["info"]["missing_libs"]
            info_lines.append(f"<b>Missing libraries:</b> {', '.join(ml[:8])}")
            info_lines.append(
                f"<span style='color:{disabled_hex};font-size:0.85em;'>Note: Some libraries may load at runtime. This check may show false positives.</span>"
            )
        if health.get("info", {}).get("no_update_url"):
            info_lines.append("<b>Update URL:</b> Not configured (optional)")
        if runnable.get("info", {}).get("apprun_size"):
            s = runnable["info"]["apprun_size"]
            info_lines.append(f"<b>AppRun size:</b> {s / 1024:.0f} KB")

        if info_lines:
            info_lbl = QLabel("<br>".join(info_lines))
            info_lbl.setWordWrap(True)
            info_lbl.setStyleSheet(
                style(
                    "background: {colors.surface}; border: 1px solid {colors.border}; border-radius: 4px; padding: {spacing.lg};"
                )
            )
            layout.addWidget(info_lbl)

        if health["warnings"] or runnable["warnings"]:
            warn_text = f"<b style='color:{mid_hex};'>Notes:</b><br>"
            for w in health["warnings"] + runnable["warnings"]:
                warn_text += f"• {w}<br>"
            warn_lbl = QLabel(warn_text)
            warn_lbl.setWordWrap(True)
            warn_lbl.setStyleSheet(
                f"background:{warn_bg};border:1px solid {warn_border};border-radius:4px;padding:8px;"
            )
            layout.addWidget(warn_lbl)

        if not has_real_issues:
            ok_lbl = QLabel(f"<span style='color:{ok_color};font-size:14px;'>App appears healthy and runnable.</span>")
            ok_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(ok_lbl)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        run_btn = QPushButton(get_icon("media-playback-start"), "Run App")
        run_btn.clicked.connect(lambda: play_and("click", dlg.accept, lambda: self._run_app(app_name)))
        btn_layout.addWidget(run_btn)
        close_btn = QPushButton(get_icon("dialog-close"), "Close")
        close_btn.clicked.connect(dlg.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        dlg.exec()

    def _show_app_info(self, app_name: str):
        app_info = self.installed_apps.get(app_name)
        if not app_info:
            return
        play_sound("interface")
        dlg = AppInfoDialog(app_name, app_info, self)
        dlg.exec()
        self.scan_installed()

    def _check_all_app_updates(self):
        registry = InstallationRegistry()
        records = registry.get_all()
        apps_with_url = [(r.name, r.update_url, r.version, r.update_channel) for r in records if r.update_url]
        if not apps_with_url:
            play_sound("info")
            QMessageBox.information(
                self,
                "No Update URLs",
                "No installed apps have an update URL configured.\n\nOpen each app's info page to set its update URL.",
            )
            return
        updated = 0
        failed = 0
        up_to_date = 0
        for name, url, current_version, channel in apps_with_url:
            if not current_version or current_version == "unknown":
                failed += 1
                continue
            try:
                info = resolve_update_source(url, current_version, channel=channel)
                if not info or not info.version:
                    failed += 1
                    continue
                from niruvi.app.self_update import compare_versions

                if compare_versions(info.version, "gt", current_version):
                    from niruvi.ui.update_wizard import UpdateWizard

                    app_info = self.installed_apps.get(name, {})
                    app_dir = app_info.get("path", "")
                    wizard = UpdateWizard(
                        name,
                        app_dir,
                        current_version,
                        url,
                        channel=channel,
                        parent=self,
                    )
                    if wizard.exec() == QDialog.DialogCode.Accepted:
                        updated += 1
                        continue
                    failed += 1
                else:
                    up_to_date += 1
            except Exception as e:
                self._show_status(f"Update check failed for {name}: {e}")
        if updated == 0 and failed == 0 and up_to_date > 0:
            play_sound("info")
            QMessageBox.information(self, "All Up to Date", "All configured apps are already up to date.")
        elif updated > 0:
            self.scan_installed()
            self._show_status(f"Updated {updated} app(s)")

    def _selected_app_names(self) -> list[str]:
        names = []
        for item in self.installed_list.selectedItems():
            key = item.data(Qt.ItemDataRole.UserRole)
            if key in self.installed_apps:
                names.append(key)
        return names

    def _update_batch_button(self):
        self.action_batch.setEnabled(len(self._selected_app_names()) >= 2)

    def _batch_update_selected(self):
        names = self._selected_app_names()
        if len(names) < 2:
            return
        play_sound("click")
        registry = InstallationRegistry()
        records = {r.name: r for r in registry.get_all()}
        tasks = []
        for name in names:
            rec = records.get(name)
            if not rec or not rec.update_url or not rec.version or rec.version == "unknown":
                continue
            tasks.append((rec.name, rec.update_url, rec.version, rec.update_channel))
        if not tasks:
            QMessageBox.information(
                self,
                "Nothing to Check",
                "None of the selected apps have both an update URL and a known version configured.",
            )
            return
        updated = 0
        failed = 0
        up_to_date = 0
        for name, url, current_version, channel in tasks:
            try:
                info = resolve_update_source(url, current_version, channel=channel)
                if not info or not info.version:
                    failed += 1
                    continue
                from niruvi.app.self_update import compare_versions

                if compare_versions(info.version, "gt", current_version):
                    from niruvi.ui.update_wizard import UpdateWizard

                    app_info = self.installed_apps.get(name, {})
                    wizard = UpdateWizard(
                        name,
                        app_info.get("path", ""),
                        current_version,
                        url,
                        channel=channel,
                        parent=self,
                    )
                    if wizard.exec() == QDialog.DialogCode.Accepted:
                        updated += 1
                    else:
                        failed += 1
                else:
                    up_to_date += 1
            except Exception as e:
                failed += 1
                self._show_status(f"Update check failed for {name}: {e}")
        if updated:
            self.scan_installed()
            self._show_status(f"Updated {updated} app(s)")
        QMessageBox.information(
            self,
            "Batch Update Finished",
            f"Updated: {updated}\nUp to date: {up_to_date}\nFailed/skipped: {failed}",
        )

    def _batch_verify_selected(self):
        names = self._selected_app_names()
        if len(names) < 2:
            return
        play_sound("click")
        passed, failed = 0, 0
        details = []
        for name in names:
            app_dir = self.installed_apps.get(name, {}).get("path", "")
            if not app_dir or not os.path.isdir(app_dir):
                failed += 1
                details.append(f"[FAIL] {name}: directory not found")
                continue
            try:
                results = verify_complete(app_dir)
            except Exception as e:
                failed += 1
                details.append(f"[FAIL] {name}: error - {e}")
                continue
            ok = all(results)
            passed += 1 if ok else 0
            failed += 0 if ok else 1
            details.append(f"{'[PASS]' if ok else '[FAIL]'} {name}: {sum(1 for r in results if r.passed)}/{len(results)} checks")
        if failed == 0:
            play_sound("notification")
            QMessageBox.information(
                self,
                "Batch Verification Passed",
                f"All {passed} selected apps passed verification.\n\n" + "\n".join(details),
            )
        else:
            play_sound("warning")
            QMessageBox.warning(
                self,
                "Batch Verification Finished",
                f"Passed: {passed}\nFailed: {failed}\n\n" + "\n".join(details),
            )

    def _batch_uninstall_selected(self):
        names = self._selected_app_names()
        if len(names) < 2:
            return
        play_sound("click")
        confirm = QMessageBox.question(
            self,
            "Batch Uninstall",
            f"Uninstall the {len(names)} selected apps?\n\n"
            + "\n".join(f"• {n}" for n in names)
            + "\n\nEach app will be removed with its install directory and registry entry.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        removed = 0
        for name in names:
            app_info = self.installed_apps.get(name)
            if not app_info:
                continue
            app_dir = app_info.get("path", "")
            if not app_dir or not os.path.isdir(app_dir):
                continue
            wizard = UninstallWizard(name, app_dir, self)
            if wizard.exec() == QDialog.DialogCode.Accepted:
                removed += 1
        if removed:
            self.scan_installed()
            self._show_status(f"Uninstalled {removed} app(s)")
            play_sound("notification")

    def _check_single_app_update(self, app_name: str, update_url: str, current_version: str):
        if not update_url:
            play_sound("info")
            QMessageBox.information(
                self,
                "No Update URL",
                f"No update URL configured for {app_name}.\n\nOpen App Info to set one.",
            )
            return
        if not current_version or current_version == "unknown":
            play_sound("warning")
            QMessageBox.information(
                self, "Unknown Version", f"The current version of {app_name} is unknown. Update check cannot proceed."
            )
            return

        app_info = self.installed_apps.get(app_name)
        app_dir = app_info.get("path", "") if app_info else ""
        if app_info and app_dir:
            from niruvi.desktop.appimageupdate import get_update_method_for_app

            method_info = get_update_method_for_app(app_dir)
            if method_info["method"] == "zsync":
                play_sound("info")
                ret = QMessageBox.question(
                    self,
                    "Delta Update Available",
                    f"An AppImageUpdate-compatible update is available for <b>{app_name}</b>.<br><br>"
                    f"Update info: <code>{method_info['update_info'][:60]}</code><br><br>"
                    "Use delta update (smaller download)?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if ret == QMessageBox.StandardButton.Yes:
                    self._zsync_update_app(app_name, app_dir)
                    return

        registry = InstallationRegistry()
        record = registry.get(app_name)
        channel = record.update_channel if record else "stable"

        from niruvi.ui.update_wizard import UpdateWizard

        wizard = UpdateWizard(
            app_name,
            app_dir,
            current_version,
            update_url,
            channel=channel,
            parent=self,
        )
        wizard.exec()

    def _download_app_update(self, app_name: str, download_url: str, latest_version: str, expected_sha256: str = ""):
        from niruvi.app.update_sources import UpdateInfo
        from niruvi.ui.update_wizard import UpdateWizard

        app_info = self.installed_apps.get(app_name)
        if app_info is None:
            return
        app_dir = app_info["path"]
        current_version = app_info.get("version", "0.0.0")
        from niruvi.desktop.installation_registry import InstallationRegistry

        record = InstallationRegistry().get(app_name)
        channel = record.update_channel if record else "stable"
        update_url = record.update_url if record else ""
        if not expected_sha256:
            # The update cannot be integrity-checked (no SHA256 published by
            # the source). Never install silently: require explicit consent.
            play_sound("warning")
            reply = QMessageBox.question(
                self,
                "Unverified Download",
                f"<b>{app_name}</b> v{latest_version} cannot be verified before "
                "installation — its publisher does not provide a SHA-256 "
                "checksum for this download.<br><br>"
                f"Source: <code>{download_url[:100]}</code><br><br>"
                "Install this <b>unverified</b> update anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                logger.info("User declined unverified update download for %s", app_name)
                return
        pre_resolved = UpdateInfo(
            version=latest_version,
            download_url=download_url,
            sha256=expected_sha256 or None,
        )
        wizard = UpdateWizard(
            app_name,
            app_dir,
            current_version,
            update_url,
            channel=channel,
            parent=self,
            update_info=pre_resolved,
        )
        wizard.exec()

    def _zsync_update_app(self, app_name: str, app_dir: str):
        """Update an app using AppImageUpdate (zsync delta update)."""
        from niruvi.desktop.appimageupdate import (
            find_appimage_in_dir,
            update_appimage_via_tool,
        )

        def _fallback_full_download():
            record = InstallationRegistry().get(app_name)
            update_url = record.update_url if record else ""
            current_version = self.installed_apps.get(app_name, {}).get("version", "")
            self._check_single_app_update(app_name, update_url, current_version)

        appimage_path = find_appimage_in_dir(app_dir)
        if not appimage_path:
            play_sound("warning")
            QMessageBox.information(
                self,
                "Original AppImage Not Found",
                f"The original AppImage file was not found in {app_dir}.\n\nFalling back to full download update.",
            )
            _fallback_full_download()
            return

        progress = QProgressDialog(f"Delta-updating {app_name}...", None, 0, 0, self)
        progress.setWindowTitle("AppImageUpdate")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()
        QApplication.processEvents()

        try:
            success, msg = update_appimage_via_tool(appimage_path)
            progress.close()

            if not success:
                play_sound("warning")
                QMessageBox.warning(
                    self,
                    "Delta Update Failed",
                    f"AppImageUpdate failed for {app_name}:<br><br><code>{msg}</code><br><br>"
                    "Falling back to full download.",
                )
                _fallback_full_download()
                return

            # Create the single .prev rollback copy (no redundant second copy).
            prev_dir = app_dir + ".prev"
            if os.path.exists(prev_dir):
                shutil.rmtree(prev_dir, ignore_errors=True)
            if os.path.isdir(app_dir):
                self._create_prev_backup(app_dir, prev_dir)

            from niruvi.core.worker import extract_appimage_sync

            extract_appimage_sync(appimage_path, app_dir)

            from niruvi.desktop.desktop_utils import get_version

            version = get_version(app_dir) or ""
            registry = InstallationRegistry()
            record = registry.get(app_name)
            if record:
                record.version = version
                registry.add(record)

            self._show_status(f"Updated {app_name} via delta update (version {version})")
            play_sound("success")
            QMessageBox.information(
                self,
                "Update Complete",
                f"<b>{app_name}</b> was updated via delta update.<br><br>Result: {msg}",
            )

        except Exception as e:
            progress.close()
            play_sound("error")
            QMessageBox.critical(self, "Update Failed", f"Delta update failed:\n{e}")

    def _open_build_dialog(self):
        play_sound("interface")
        wizard = BuildWizard(self)
        if wizard.exec() == QDialog.DialogCode.Accepted:
            self._show_status("AppImage built successfully!")
        else:
            self._show_status("Build cancelled or failed")

    def _check_all_updates(self):
        check_for_updates(self)

    def _start_background_updater(self):
        interval = _settings.get("update_check_interval", "weekly")
        from niruvi.app.background_updater import INTERVAL_OPTIONS

        seconds = INTERVAL_OPTIONS.get(interval, 604800)
        auto_update = _settings.get("auto_update_apps", False)
        if seconds > 0:
            self._background_updater.start(seconds, auto_update)

    def _on_background_update_found(self, result):
        if getattr(self, "_closing", False):
            return
        play_sound("notification")
        from niruvi.core.plugins import emit

        emit(
            "update_available",
            app_name=result.app_name,
            version=result.latest_version,
            source_type=result.source_type,
        )

        app = QApplication.instance()
        if self._tray is not None and self._tray.isVisible():
            self._tray.showMessage(
                "Update Available",
                f"{result.app_name} v{result.latest_version} is available",
                QSystemTrayIcon.MessageIcon.Information,
                10000,
            )
            self._last_update_result = result
            return
        has_tray = (
            app
            and hasattr(app, "desktop")
            and QSystemTrayIcon.isSystemTrayAvailable()
            and _settings.get("tray_enabled", True)
        )
        if has_tray:
            try:
                active_window = app.activeWindow() if isinstance(app, QApplication) else None
                tray = QSystemTrayIcon(
                    get_icon("emblem-downloads", "emblem-important"),
                    active_window or self,
                )
                tray.setToolTip(f"Update available for {result.app_name}")
                menu = QMenu()
                show_action = self._add_action_text(menu, f"Show {result.app_name} update")
                show_action.triggered.connect(lambda: self._show_app_update_notification(result))
                tray.setContextMenu(menu)
                tray.show()
                tray.showMessage(
                    "Update Available",
                    f"{result.app_name} v{result.latest_version} is available",
                    QSystemTrayIcon.MessageIcon.Information,
                    5000,
                )
                self._tray_icons.append(tray)

                def _cleanup_tray():
                    if getattr(self, "_closing", False):
                        return
                    try:
                        self._tray_icons.remove(tray)
                    except ValueError:
                        pass
                    tray.hide()
                    tray.deleteLater()

                QTimer.singleShot(15000, _cleanup_tray)
                return
            except Exception as e:
                logger.debug("Failed to show tray notification: %s", e, exc_info=True)
        play_sound("notification")
        reply = QMessageBox.question(
            self,
            "Update Available",
            f"{result.app_name} v{result.latest_version} is available "
            f"(current: v{result.current_version}).\n\n"
            f"Source: {result.source_type}\n"
            + (
                "(no SHA-256 checksum published - download cannot be verified)\n"
                if not getattr(result, "sha256", None)
                else ""
            )
            + (f"\n{result.changelog[:300]}" if result.changelog else "")
            + "\n\nDownload and install now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._download_app_update(result.app_name, result.download_url, result.latest_version, result.sha256 or "")

    def _show_app_update_notification(self, result):
        app_info = self.installed_apps.get(result.app_name)
        if app_info:
            self._show_app_info(result.app_name)

    def _export_app_list(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export App List",
            "niruvi-apps.json",
            "JSON files (*.json);;All files (*)",
        )
        if not file_path:
            return
        registry = InstallationRegistry()
        records = registry.get_all()
        data = [r.to_dict() for r in records]
        try:
            with open(file_path, "w") as f:
                json.dump(data, f, indent=2)
            play_sound("success")
            self._show_status(f"Exported {len(data)} app(s) to {file_path}")
        except OSError as e:
            play_sound("error")
            QMessageBox.critical(self, "Export Failed", str(e))

    def _import_app_list(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import App List",
            "",
            "JSON files (*.json);;All files (*)",
        )
        if not file_path:
            return
        try:
            with open(file_path) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            play_sound("error")
            QMessageBox.critical(self, "Import Failed", f"Could not read file:\n{e}")
            return
        if not isinstance(data, list):
            play_sound("error")
            QMessageBox.critical(self, "Import Failed", "Invalid format: expected a list of apps.")
            return
        registry = InstallationRegistry()
        imported = 0
        skipped = 0
        for item in data:
            name = item.get("name", "")
            if not name:
                skipped += 1
                continue
            if registry.get(name):
                skipped += 1
                continue
            record = InstallationRecord.from_dict(item)
            registry.add(record)
            imported += 1
        self.scan_installed()
        play_sound("success" if imported > 0 else "info")
        self._show_status(f"Imported {imported} app(s)" + (f", skipped {skipped}" if skipped else ""))

    def _open_store(self):
        play_sound("click")
        from niruvi.ui.store_dialog import StoreDialog

        dialog = StoreDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.scan_installed()

    def open_settings(self):
        play_sound("interface")
        dialog = SettingsDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.scan_installed()
            self._sync_tray()

    def _show_help(self):
        play_sound("interface")
        dlg = HelpDialog(self)
        dlg.exec()

    def _show_device_info(self):
        play_sound("interface")
        dlg = DeviceInfoDialog(self)
        dlg.exec()

    def _show_storage_analyzer(self):
        play_sound("interface")
        from niruvi.ui.storage_dialog import StorageDialog

        apps = [(name, info.get("path", "")) for name, info in self.installed_apps.items()]
        dlg = StorageDialog(apps, self)
        dlg.exec()

    def _show_permissions(self, app_id: str | None = None):
        """Open the App Info dialog's Permissions tab for one app.

        With newer Niruvi builds permissions live in the per-app info dialog;
        this keeps the Tools entry working. Only when no specific app can be
        identified (nothing selected) does the multi-app manager open.
        """
        play_sound("interface")
        app_id = app_id or (self._selected_app_names()[:1] or [None])[0]
        if app_id:
            info = self.installed_apps.get(app_id)
            if info:
                from niruvi.ui.app_info_dialog import AppInfoDialog

                dlg = AppInfoDialog(app_id, info, self)
                dlg._on_tab_button_clicked(1)  # Permissions tab
                dlg.exec()
                self.scan_installed()
                return
        from niruvi.ui.permissions_dialog import PermissionsWindow

        apps = [
            {
                "id": name,
                "name": info.get("display_name", name),
                "path": info.get("path", ""),
                "icon_path": info.get("icon_path", ""),
            }
            for name, info in self.installed_apps.items()
        ]
        if not apps:
            QMessageBox.information(self, "Permissions", "No applications are installed yet.")
            return
        dlg = PermissionsWindow(apps, self)
        dlg.exec()

    def _show_report_page(self):
        play_sound("interface")
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout

        dlg = QDialog(self)
        dlg.setWindowTitle("Report Issue")
        dlg.setMinimumSize(520, 400)
        layout = QVBoxLayout(dlg)
        layout.addWidget(ReportPage(dlg))
        btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn.rejected.connect(lambda: play_and("navigation", dlg.accept))
        layout.addWidget(btn)
        dlg.exec()

    def _show_support(self):
        from niruvi.ui.donate_dialog import DonateDialog

        play_sound("info")
        dlg = DonateDialog(self)
        dlg.exec()

    def _show_license(self):
        play_sound("interface")
        dlg = HelpDialog(self, initial_page="License")
        dlg.exec()

    def _show_about(self):
        from niruvi._version import __version__
        from niruvi.desktop.brand import brand_icon

        play_sound("info")

        dlg = QDialog(self)
        dlg.setWindowTitle("About Niruvi")
        dlg.setMinimumWidth(420)
        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(24, 20, 24, 16)
        outer.setSpacing(14)

        # Same logo the window icon, installer and desktop entry use.
        logo = QLabel()
        logo.setPixmap(icon_pixmap(brand_icon(), ICON_SIZE_HERO))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setAccessibleName("Niruvi logo")
        outer.addWidget(logo)

        title = QLabel("Niruvi")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        outer.addWidget(title)

        version = QLabel(f"Version {__version__}")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version.setStyleSheet("color: palette(placeholder-text);")
        outer.addWidget(version)

        blurb = QLabel(
            "A universal Linux AppImage manager for installing,<br>"
            "updating, uninstalling, and building AppImages."
        )
        blurb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        blurb.setWordWrap(True)
        outer.addWidget(blurb)

        features = QLabel(
            "<b>Features</b><br>"
            "• Install, update, and manage AppImages<br>"
            "• Build AppImages from DEB, RPM, and tar packages<br>"
            "• Create desktop entries and shortcuts<br>"
            "• Security scanner with malware detection<br>"
            "• Per-application permissions and sandboxing<br>"
            "• Backup/rollback on update or overwrite<br>"
            "• Portable home &amp; config folder support<br>"
            "• Drag-and-drop install<br>"
            "• Search and filter installed apps"
        )
        features.setWordWrap(True)
        features.setTextFormat(Qt.TextFormat.RichText)
        outer.addWidget(features)

        license_box = QGroupBox("License")
        license_layout = QVBoxLayout(license_box)
        license_text = QLabel(
            "GNU General Public License v3<br>"
            "Copyright © 2026 putinservai-cyber<br><br>"
            "This program is free software; you can redistribute it and/or "
            "modify it under the terms of the GNU General Public License as "
            "published by the Free Software Foundation; version 3 of the License."
        )
        license_text.setWordWrap(True)
        license_text.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        license_layout.addWidget(license_text)
        outer.addWidget(license_box)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dlg.reject)
        buttons.accepted.connect(dlg.accept)
        outer.addWidget(buttons)
        dlg.exec()
