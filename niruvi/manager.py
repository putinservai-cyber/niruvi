import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request

import logging

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem,
    QMessageBox, QMenu, QFileDialog, QProgressDialog,
    QDialog, QLabel, QPushButton,
    QLineEdit, QComboBox, QFrame, QSizePolicy,
    QApplication, QProgressBar, QGraphicsDropShadowEffect,
)
from PyQt6.QtCore import Qt, QSize, QThread, QEventLoop, pyqtSignal, QTimer
from PyQt6.QtGui import QAction, QPixmap, QIcon, QDragEnterEvent, QDropEvent, QPalette
from pathlib import Path

from niruvi._version import __app_name__
from niruvi.settings import (
    get_settings,
    DEFAULT_INSTALL_DIR,
    SettingsDialog,
)
from niruvi.app_info_dialog import AppInfoDialog
from niruvi.worker import ExtractionWorker, _is_removable_path, _ensure_local
from niruvi.desktop_utils import (
    get_version,
    create_desktop_entry,
    create_desktop_shortcut,
    find_desktop_for_app,
    find_desktop_shortcut,
    parse_desktop_file,
)
from niruvi.wizard import InstallWizard
from niruvi.build_dialog import BuildDialog
from niruvi.help_dialog import HelpDialog
from niruvi.device_info import DeviceInfoDialog
from niruvi.uninstall_dialog import UninstallWizard
from niruvi.report_page import ReportPage
from niruvi.utils import get_icon
from niruvi.hooks import run_hooks, ensure_hooks_dir
from niruvi.sandbox import Shield, ShieldConfig
from niruvi.health_check import check_app_health
from niruvi.installation_registry import InstallationRegistry
from niruvi.icon_utils import get_pixmap_from_file, to_png_bytes
from niruvi.appimage_metadata import AppImageMetadata
from niruvi.appimage_assets import extract_metadata
from niruvi.self_update import check_for_updates
from niruvi.background_updater import BackgroundUpdater
from niruvi.update_sources import resolve_update_source
from niruvi.installation_registry import InstallationRecord
from niruvi.settings import _settings
from niruvi.sound_manager import play as play_sound, install_button_filter, uninstall_button_filter, install_menu_sound

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
                    lines.append(f"<b>OS:</b> {line.split('=', 1)[1].strip().strip('\"')}")
                    break
    except Exception:
        pass
    lines.append(f"<b>Kernel:</b> {platform.version()}")
    try:
        result = subprocess.run(["glxinfo", "-B"], capture_output=True, text=True, timeout=5)
        for line in result.stdout.splitlines():
            if "OpenGL renderer" in line:
                lines.append(f"<b>GPU:</b> {line.split(':', 1)[1].strip()}")
                break
    except Exception:
        pass
    try:
        result = subprocess.run(["rpm", "-q", "mesa-dri-drivers", "--qf", "%{VERSION}"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            lines.append(f"<b>Mesa:</b> {result.stdout.strip()}")
    except Exception:
        pass
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


class MetadataWorker(QThread):
    metadata_ready = pyqtSignal(dict, object)

    def __init__(self, appimage_path, parent=None):
        super().__init__(parent)
        self.appimage_path = appimage_path

    def run(self):
        info, icon_data = get_appimage_metadata(self.appimage_path)
        self.metadata_ready.emit(info, icon_data)


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


class AppManager(QMainWindow):
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
        from niruvi.sound_manager import _init as _init_sound
        _init_sound()
        from niruvi.hooks import ensure_hooks_dir
        ensure_hooks_dir()
        install_button_filter()
        self._init_ui()
        self._scanning = False
        self.scan_installed()
        self._start_background_updater()

    def _init_ui(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu(get_icon("document-open"), "File")
        install_menu_sound(file_menu)

        install_action = QAction(get_icon("list-add"), "Install...", self)
        install_action.setShortcut("Ctrl+I")
        install_action.triggered.connect(self.run_install_wizard)
        file_menu.addAction(install_action)

        settings_action = QAction(get_icon("preferences-system"), "Settings...", self)
        settings_action.triggered.connect(self.open_settings)
        file_menu.addAction(settings_action)

        file_menu.addSeparator()

        export_action = QAction(get_icon("document-save-as"), "Export App List...", self)
        export_action.triggered.connect(self._export_app_list)
        file_menu.addAction(export_action)

        import_action = QAction(get_icon("document-open"), "Import App List...", self)
        import_action.triggered.connect(self._import_app_list)
        file_menu.addAction(import_action)

        file_menu.addSeparator()

        quit_action = QAction(get_icon("application-exit"), "Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        tools_menu = menubar.addMenu(get_icon("applications-utilities", "preferences-other", "emblem-system"), "Tools")
        install_menu_sound(tools_menu)

        build_action = QAction(get_icon("emblem-system", "applications-utilities", "document-export"), "Build AppImage...", self)
        build_action.triggered.connect(self._open_build_dialog)
        tools_menu.addAction(build_action)

        device_action = QAction(get_icon("computer", "video-display", "system-search"), "Device Info...", self)
        device_action.triggered.connect(self._show_device_info)
        tools_menu.addAction(device_action)

        tools_menu.addSeparator()

        check_updates_action = QAction(get_icon("emblem-downloads", "system-software-update", "download", "document-save"), "Check for Niruvi Updates...", self)
        check_updates_action.triggered.connect(lambda: check_for_updates(self))
        tools_menu.addAction(check_updates_action)

        check_all_action = QAction(get_icon("network-server", "emblem-downloads"), "Check All Apps for Updates...", self)
        check_all_action.triggered.connect(self._check_all_app_updates)
        tools_menu.addAction(check_all_action)

        refresh_action = QAction(get_icon("view-refresh"), "Refresh Installed", self)
        refresh_action.setShortcut("Ctrl+R")
        refresh_action.triggered.connect(self.scan_installed)
        tools_menu.addAction(refresh_action)

        help_menu = menubar.addMenu(get_icon("help-contents"), "Help")
        install_menu_sound(help_menu)

        help_action = QAction(get_icon("help-contents"), "Help...", self)
        help_action.setShortcut("F1")
        help_action.triggered.connect(self._show_help)
        help_menu.addAction(help_action)

        report_action = QAction(
            get_icon("bug", "tools-report-bug", "dialog-warning"), "Report Issue...", self
        )
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

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        # --- Header: count + action buttons ---
        header_layout = QHBoxLayout()
        self.installed_count_label = QLabel("No apps installed")
        self.installed_count_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        header_layout.addWidget(self.installed_count_label)
        header_layout.addStretch()

        self.btn_build = QPushButton(get_icon("emblem-system"), "Build")
        self.btn_build.setToolTip("Build an AppImage from a DEB, RPM, or tar archive")
        self.btn_build.clicked.connect(self._open_build_dialog)
        header_layout.addWidget(self.btn_build)

        self.btn_refresh = QPushButton(get_icon("view-refresh"), "Refresh")
        self.btn_refresh.setToolTip("Re-scan the install directory for installed apps")
        self.btn_refresh.clicked.connect(self.scan_installed)
        header_layout.addWidget(self.btn_refresh)

        self.btn_install = QPushButton(get_icon("list-add"), "Install AppImage...")
        self.btn_install.setToolTip("Browse for an AppImage file to install")
        self.btn_install.clicked.connect(self.run_install_wizard)
        header_layout.addWidget(self.btn_install)

        layout.addLayout(header_layout)

        # --- Search + sort ---
        search_sort_layout = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search installed apps...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setToolTip("Type to filter the app list by name")
        self.search_edit.addAction(get_icon("edit-find"), QLineEdit.ActionPosition.LeadingPosition)
        self.search_edit.textChanged.connect(self._filter_apps)
        search_sort_layout.addWidget(self.search_edit)

        self.sort_combo = QComboBox()
        self.sort_combo.addItems([
            "Sort by Name",
            "Sort by Version",
            "Sort by Size",
            "Sort by Install Date",
        ])
        self.sort_combo.currentIndexChanged.connect(self._sort_apps)
        self.sort_combo.setToolTip("Change how installed apps are ordered")
        search_sort_layout.addWidget(self.sort_combo)
        layout.addLayout(search_sort_layout)

        # --- App list ---
        self.installed_list = QListWidget()
        self.installed_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.installed_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.installed_list.customContextMenuRequested.connect(self._show_context_menu)
        self.installed_list.setIconSize(QSize(32, 32))
        self.installed_list.setSpacing(4)
        self.installed_list.setFrameShape(QFrame.Shape.NoFrame)
        self.installed_list.itemDoubleClicked.connect(self._on_app_double_clicked)
        layout.addWidget(self.installed_list, 1)

        # --- Drop hint overlay ---
        disabled_hex = self.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text).name()
        self.drop_hint = QLabel(
            '<div style="text-align: center; padding: 40px;">'
            '<br><br>'
            '<b style="font-size: 16px;">Drop AppImage files here</b><br>'
            f'<span style="color: {disabled_hex};">to install them automatically</span>'
            '</div>'
        )
        self.drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_hint.setStyleSheet(
            "background: palette(window); border: 2px dashed palette(mid); "
            "border-radius: 12px; margin: 8px;"
        )
        self.drop_hint.setVisible(False)
        layout.addWidget(self.drop_hint, 1)

        # --- Empty state (shown when no apps installed) ---
        self.empty_widget = QWidget()
        self.empty_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        empty_layout = QVBoxLayout(self.empty_widget)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.setSpacing(12)

        empty_icon_label = QLabel()
        app_icon = get_icon("package-x-generic", "application-x-archive", "application-x-executable")
        empty_icon_label.setPixmap(app_icon.pixmap(72, 72))
        empty_icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_icon_label)

        empty_title = QLabel("<h2>No AppImages installed</h2>")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_title)

        empty_desc = QLabel(
            "Drag an AppImage file here or click the button below to get started."
        )
        empty_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_desc.setWordWrap(True)
        empty_layout.addWidget(empty_desc)

        self.empty_install_btn = QPushButton(get_icon("list-add"), "Install Your First AppImage")
        self.empty_install_btn.setFixedWidth(280)
        self.empty_install_btn.setStyleSheet(
            "QPushButton { padding: 10px 24px; font-size: 14px; }"
        )
        self.empty_install_btn.clicked.connect(self.run_install_wizard)
        btn_wrapper = QHBoxLayout()
        btn_wrapper.addStretch()
        btn_wrapper.addWidget(self.empty_install_btn)
        btn_wrapper.addStretch()
        empty_layout.addLayout(btn_wrapper)

        layout.addWidget(self.empty_widget, 1)

        self._status_bar = self.statusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Ready")

    def closeEvent(self, event):
        self._background_updater.stop()
        if self._metadata_worker and self._metadata_worker.isRunning():
            self._metadata_worker.quit()
            self._metadata_worker.wait(1000)
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(1000)
        uninstall_button_filter()
        super().closeEvent(event)

    def _add_app_to_list(self, key: str, app_dir: str, version: str, display_name: str | None = None, icon_path: str | None = None, update_url: str = "", architecture: str = "", display_name_override: str = "", custom_icon_path: str = "", health_info: dict | None = None):
        if display_name is None:
            display_name = key
        version_str = version if version and version != "unknown" else ""

        # Distinguish multiple instances with the same display name
        if key != display_name:
            label = f"{display_name}  ({key})"
        else:
            label = display_name
        text = label + (f"  (v{version_str})" if version_str else "")
        list_item = QListWidgetItem(text)
        list_item.setData(Qt.ItemDataRole.UserRole, key)
        tooltip = (
            f"Path: {app_dir}\n"
            f"Version: {version_str or 'unknown'}\n"
            f"Name: {key}"
        )
        if health_info:
            if health_info.get("issues"):
                tooltip += f"\nIssues: {'; '.join(health_info['issues'])}"
            if health_info.get("warnings"):
                tooltip += f"\nWarnings: {'; '.join(health_info['warnings'])}"
        if not update_url:
            tooltip += "\nNo update URL configured"
        list_item.setToolTip(tooltip)
        icon_set = False
        if icon_path:
            pixmap = get_pixmap_from_file(icon_path, 32)
            if pixmap and not pixmap.isNull():
                list_item.setIcon(QIcon(pixmap))
                icon_set = True
        if not icon_set:
            icon_from_theme = get_icon("package-x-generic", "application-x-archive", "application-x-executable")
            if icon_from_theme and not icon_from_theme.isNull():
                list_item.setIcon(icon_from_theme)
        self.installed_list.addItem(list_item)
        app_size = 0
        install_time = 0.0
        if os.path.isdir(app_dir):
            try:
                install_time = os.path.getctime(app_dir)
                for root, dirs, files in os.walk(app_dir):
                    for f in files:
                        try:
                            app_size += os.path.getsize(os.path.join(root, f))
                        except OSError:
                            pass
            except OSError:
                pass
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
        }

    def scan_installed(self):
        if getattr(self, '_scanning', False):
            return
        self._scanning = True
        self.btn_refresh.setEnabled(False)
        QApplication.processEvents()
        self.installed_apps.clear()
        self.installed_list.clear()
        install_dir = get_settings().get("install_dir", DEFAULT_INSTALL_DIR)
        seen = set()
        registry = InstallationRegistry()

        if os.path.isdir(install_dir):
            real_map = {}
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

            for item, is_link, app_dir in real_map.values():
                seen.add(item)
                apprun = os.path.join(app_dir, "AppRun")
                if not os.path.isfile(apprun) or not os.access(apprun, os.X_OK):
                    continue
                try:
                    version = get_version(app_dir)
                    icon_path = self._find_app_icon(app_dir)
                    desktop_info = parse_desktop_file(
                        os.path.join(app_dir, f"{item}.desktop")
                    ) if os.path.exists(os.path.join(app_dir, f"{item}.desktop")) else {}
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
                    self._add_app_to_list(item, app_dir, version, display_name, icon_path, update_url, arch, dn_override, cust_icon, health_info)
                except Exception as e:
                    logging.error("Failed to scan app %s: %s", item, e)
                    continue

        stale = []
        for record in registry.get_all():
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
            cust_icon = record.custom_icon_path or icon_path
            self._add_app_to_list(record.name, app_dir, version, display_name, cust_icon or icon_path, record.update_url, record.architecture, record.display_name_override, record.custom_icon_path)
        for name in stale:
            registry.remove(name)

        self._cleanup_mtp_orphans()

        count = len(self.installed_apps)
        has_apps = count > 0
        self.installed_count_label.setText(
            f"{count} app{'s' if count != 1 else ''} installed" if has_apps else "No apps installed"
        )
        self.installed_list.setVisible(has_apps)
        self.search_edit.setVisible(has_apps)
        self.sort_combo.setVisible(has_apps)
        self.drop_hint.setVisible(False)
        self.empty_widget.setVisible(not has_apps)
        self._status_bar.showMessage(f"Found {count} installed app{'s' if count != 1 else ''}" if has_apps else "Ready — no AppImages installed yet")
        self._scanning = False
        self.btn_refresh.setEnabled(True)

    def _filter_apps(self, text: str):
        for i in range(self.installed_list.count()):
            item = self.installed_list.item(i)
            if item:
                item.setHidden(text.lower() not in item.text().lower())

    def _sort_apps(self, idx: int):
        items = []
        while self.installed_list.count():
            item = self.installed_list.takeItem(0)
            if item:
                key = item.data(Qt.ItemDataRole.UserRole)
                items.append((key, item))
        if idx == 0:
            items.sort(key=lambda x: x[1].text().lower())
        elif idx == 1:
            items.sort(key=lambda x: self.installed_apps.get(x[0], {}).get("version", ""))
        elif idx == 2:
            items.sort(key=lambda x: self.installed_apps.get(x[0], {}).get("size", 0))
        else:
            items.sort(key=lambda x: self.installed_apps.get(x[0], {}).get("install_date", 0.0))
        for _, item in items:
            self.installed_list.addItem(item)

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
            self, "Orphaned Installation",
            "Found apps installed to a phone or removable drive that is no longer connected.<br><br>"
            f"{names}<br><br>"
            "Do you want to remove them from the installed list?<br>"
            "<small>(Files on the disconnected device cannot be cleaned up until it is reconnected.)</small>",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            registry = InstallationRegistry()
            for name, path in to_remove:
                self.installed_apps.pop(name, None)
                registry.remove(name)
            self._status_bar.showMessage(f"Removed {len(to_remove)} orphaned entr{'y' if len(to_remove) == 1 else 'ies'} from the installed list")

    def _find_app_icon(self, app_dir: str) -> str | None:
        from niruvi.desktop_utils import find_icon_in_appdir
        for f in os.listdir(app_dir):
            if f.endswith(".desktop"):
                try:
                    with open(os.path.join(app_dir, f)) as df:
                        for line in df:
                            if line.startswith("Icon="):
                                icon_name = line.split("=", 1)[1].strip()
                                path = find_icon_in_appdir(app_dir, icon_name)
                                if path:
                                    return path
                except OSError:
                    pass
        common_names = {"icon.png", "icon.svg", "logo.png", "logo.svg",
                        "appicon.png", "appicon.svg", "AppIcon.png",
                        "AppIcon.svg", "application.png"}
        for root, _, files in os.walk(app_dir):
            for f in files:
                if f in common_names:
                    return os.path.join(root, f)
        for ext in (".png", ".svg", ".xpm", ".ico", ".icns"):
            for root, _, files in os.walk(app_dir):
                for f in files:
                    if f.endswith(ext) and not f.startswith("."):
                        return os.path.join(root, f)
        return None

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile() and url.path().lower().endswith('.appimage'):
                    self.drop_hint.setVisible(True)
                    self.installed_list.setVisible(False)
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dragLeaveEvent(self, event):
        self.drop_hint.setVisible(False)
        self.installed_list.setVisible(len(self.installed_apps) > 0)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent):
        self.drop_hint.setVisible(False)
        urls = []
        for url in event.mimeData().urls():
            if url.isLocalFile() and url.path().lower().endswith('.appimage'):
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
        if registry.lookup_by_path(path):
            return True
        return False

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
        self._metadata_worker.start()
        loop.exec()

        progress.close()
        worker = self._metadata_worker
        self._metadata_worker = None
        info = result.get("info", {"Name": Path(path).stem})
        icon_data = result.get("icon_data")

        app_name = info.get("Name", Path(path).stem)

        if self._is_already_installed(app_name, path):
            dlg = QDialog(self)
            dlg.setWindowTitle("Already Installed")
            dlg.setFixedSize(480, 260)
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

        wizard = InstallWizard(path, self, appimage_info=info, icon_data=icon_data)
        wizard.exec()
        self.scan_installed()

        if _settings.get("auto_remove_source"):
            self._cleanup_source_appimage(path, app_name)

    def _cleanup_source_appimage(self, path: str, app_name: str):
        """Delete the source AppImage after successful install if setting enabled."""
        # Skip temp downloads (from catalog) and removable-media copies
        if "/tmp/" in path or "/niruvi_local_copy/" in path:
            return
        if not os.path.isfile(path):
            return
        try:
            os.unlink(path)
            self._status_bar.showMessage(f"Deleted source file: {Path(path).name}")
        except OSError as e:
            logging.warning("Could not delete source AppImage %s: %s", path, e)

    def _batch_install(self, paths: list[str]):
        """Install multiple AppImages sequentially."""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QDialogButtonBox
        from niruvi.worker import InstallQueueWorker

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
        btn_box.rejected.connect(dlg.reject)
        layout.addWidget(btn_box)

        install_dir = get_settings()["install_dir"]
        queue_worker = InstallQueueWorker()

        for p in paths:
            name = Path(p).stem
            dest = os.path.join(install_dir, name)
            queue_worker.add_task({
                "appimage_path": p,
                "dest_dir": dest,
                "app_name": name,
            })

        def on_task_started(app_name, idx):
            self._batch_status.setText(f"[{idx}/{len(paths)}] Installing {app_name}...")
            self._batch_progress.setValue(idx - 1)

        def on_task_finished(app_name):
            self._batch_status.setText(f"Finished: {app_name}")
            self._batch_progress.setValue(
                self._batch_progress.value() + 1
            )

        def on_task_error(msg):
            self._batch_status.setText(f"Error: {msg}")

        def on_all_finished():
            self._batch_status.setText("All installations complete!")
            self.scan_installed()

        queue_worker.task_started.connect(on_task_started)
        queue_worker.task_finished.connect(on_task_finished)
        queue_worker.task_error.connect(on_task_error)
        queue_worker.all_finished.connect(on_all_finished)

        dlg.finished.connect(lambda: queue_worker.stop() if queue_worker.isRunning() else None)
        queue_worker.start()
        dlg.exec()

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

    def _show_context_menu(self, pos):
        item = self.installed_list.itemAt(pos)
        if not item:
            return
        app_name = item.data(Qt.ItemDataRole.UserRole)
        if app_name not in self.installed_apps:
            return

        app_info = self.installed_apps[app_name]
        is_self = (app_name == __app_name__)
        menu = QMenu()
        shadow = QGraphicsDropShadowEffect(menu)
        shadow.setBlurRadius(12)
        shadow.setOffset(2, 2)
        shadow.setColor(self.palette().shadow().color())
        menu.setGraphicsEffect(shadow)
        install_menu_sound(menu)
        info_action = menu.addAction(get_icon("help-about", "dialog-information"), "App Info")
        menu.addSeparator()
        run_action = None
        if not is_self:
            run_action = menu.addAction(get_icon("media-playback-start"), "Run")
        update_action = menu.addAction(get_icon("emblem-downloads"), "Update...")
        has_url = bool(app_info.get("update_url"))
        check_update_action = menu.addAction(
            get_icon("network-server", "emblem-downloads"), "Check for Updates"
        ) if has_url else None
        uninstall_action = menu.addAction(get_icon("edit-delete"), "Uninstall")
        open_folder_action = menu.addAction(get_icon("folder-open"), "Open Folder")
        menu.addSeparator()
        has_shortcut = bool(app_info.get("desktop_shortcut"))
        shortcut_text = "Remove Desktop Shortcut" if has_shortcut else "Create Desktop Shortcut"
        shortcut_action = menu.addAction(get_icon("user-desktop"), shortcut_text)
        hooks_action = menu.addAction(get_icon("text-x-script", "applications-system", "utilities-terminal", "folder-open"), "Edit Hooks for This App")
        menu.addSeparator()

        action = menu.exec(self.installed_list.mapToGlobal(pos))
        if action == info_action:
            self._show_app_info(app_name)
        elif action == run_action:
            self._run_app(app_name)
        elif action == update_action:
            self._update_app(app_name)
        elif check_update_action and action == check_update_action:
            app_info = self.installed_apps.get(app_name, {})
            current_version = app_info.get("version", "")
            update_url = app_info.get("update_url", "")
            self._check_single_app_update(app_name, update_url, current_version)
        elif action == uninstall_action:
            self._uninstall_app(app_name)
        elif action == open_folder_action:
            self._open_folder(app_name)
        elif action == shortcut_action:
            if has_shortcut:
                self._remove_desktop_shortcut(app_name)
            else:
                self._create_desktop_shortcut(app_name)
        elif action == hooks_action:
            self._open_app_hooks(app_name)

    def _on_app_double_clicked(self, item):
        app_name = item.data(Qt.ItemDataRole.UserRole)
        if app_name and app_name != __app_name__:
            play_sound("click")
            self._run_app(app_name)

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
            "background: palette(button); border: 1px solid palette(mid); "
            "border-radius: 4px; padding: 8px; font-family: monospace; font-size: 11px;"
        )
        layout.addWidget(detail_lbl)

        # Run diagnostics inline
        from niruvi.health_check import check_fuse_available, check_app_runnable
        fuse = check_fuse_available()
        diag = check_app_runnable(app_name, app_dir)

        sys_lines = []
        sys_lines.append(f"<b>FUSE available:</b> {'Yes' if fuse else 'No'}")
        if diag.get("info", {}).get("missing_libs"):
            ml = diag["info"]["missing_libs"]
            sys_lines.append(f"<b>Missing libraries:</b> {', '.join(ml[:5])}")
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
        diagnose_btn.clicked.connect(lambda: (dlg.accept(), self._show_app_diagnostics(app_name)))
        btn_layout.addWidget(diagnose_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        dlg.exec()

    def _run_app(self, app_name: str):
        app_info = self.installed_apps[app_name]
        app_dir = app_info["path"]
        registry = InstallationRegistry()
        record = registry.get(app_name)
        env = os.environ.copy()
        if record and record.env_vars:
            env.update(record.env_vars)
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

        # ── Pre-flight diagnostics ──
        from niruvi.health_check import check_app_runnable, check_fuse_available
        diag = check_app_runnable(app_name, app_dir)
        if not diag["healthy"]:
            diag_issues = list(diag["issues"])
            diag_warnings = list(diag["warnings"])
            fuse_ok = check_fuse_available()
            if not fuse_ok and not diag.get("info", {}).get("missing_libs"):
                self._run_app_fallback(app_name, app_dir, env, record)
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
                self._run_app_fallback(app_name, app_dir, env, record)
            return

        unsandboxed = "--unsandboxed" in sys.argv
        hook_results = run_hooks(app_name, app_dir, env)
        for hr in hook_results:
            if hr["returncode"] != 0:
                logging.warning("Hook %s failed: %s", hr["hook"], hr["stderr"])
        cmd = [apprun]
        if record and record.run_args:
            import shlex
            cmd.extend(shlex.split(record.run_args))
        try:
            if not unsandboxed and record and record.sandbox_config.get("enabled", False):
                sc = ShieldConfig.from_dict(record.sandbox_config)
                sb = Shield(sc)
                p = sb.run(cmd, cwd=app_dir, env=env)
                if p is None:
                    p = subprocess.Popen(
                        cmd, cwd=app_dir, env=env, start_new_session=True,
                    )
            else:
                p = subprocess.Popen(
                    cmd,
                    cwd=app_dir,
                    env=env,
                    start_new_session=True,
                )
            _track_detached(p)
            self._status_bar.showMessage(f"Running {app_name}")
            # Post-launch quick health check: wait briefly, detect immediate crash
            self._monitor_launch(app_name, p)
        except OSError as e:
            self._show_run_error(app_name, app_dir, str(e))
            self._run_app_fallback(app_name, app_dir, env, record)

    def _monitor_launch(self, app_name: str, proc: subprocess.Popen):
        """Check if process crashes immediately after launch (main-thread safe)."""
        def _on_timeout():
            try:
                proc.wait(timeout=0)
                if proc.returncode != 0 and proc.returncode is not None:
                    from PyQt6.QtWidgets import QMessageBox
                    play_sound("warning")
                    QMessageBox.warning(
                        self, f"{app_name} exited unexpectedly",
                        f"<b>{app_name}</b> exited quickly (code {proc.returncode}).<br><br>"
                        "This typically means:<br>"
                        "• Missing system libraries<br>"
                        "• Incompatible architecture<br>"
                        "• Corrupted AppImage<br><br>"
                        "Try reinstalling the app or check its App Info for diagnostics."
                    )
            except subprocess.TimeoutExpired:
                pass
        QTimer.singleShot(3000, _on_timeout)

    def _run_app_fallback(self, app_name: str, app_dir: str,
                          env: dict | None = None, record=None):
        appimage_path = self._find_appimage_in_dir(app_dir)
        if appimage_path and os.path.isfile(appimage_path):
            ret = self._try_extract_and_run(appimage_path, app_name, env, record)
            if ret:
                return
        ret = self._try_extract_and_run_temp(app_dir, app_name, env, record)
        if ret:
            return
        self._show_run_error(app_name, app_dir, "All launch methods failed (FUSE, namespace, extraction).")

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

    def _try_extract_and_run(self, appimage_path: str, app_name: str,
                              env: dict | None = None, record=None) -> bool:
        try:
            from niruvi.health_check import check_namespace_available
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
                env=self._apply_portable_env(env or os.environ.copy(), os.path.dirname(appimage_path), record),
                start_new_session=True,
            )
            _track_detached(p)
            self._status_bar.showMessage(f"Running {app_name} (namespace mode)")
            return True
        except OSError:
            return False

    _temp_launch_dirs: list[str] = []

    def _try_extract_and_run_temp(self, app_dir: str, app_name: str,
                                    env: dict | None = None, record=None) -> bool:
        import tempfile
        import shutil
        import atexit
        tmp = tempfile.mkdtemp(prefix=f"niruvi-{app_name}-")
        try:
            appimage_path = self._find_appimage_in_dir(app_dir)
            if appimage_path and os.path.isfile(appimage_path):
                from niruvi.worker import extract_appimage_sync
                extract_appimage_sync(appimage_path, tmp)
                apprun = os.path.join(tmp, "AppRun")
                if os.path.isfile(apprun):
                    cmd = [apprun]
                    if record and record.run_args:
                        import shlex
                        cmd.extend(shlex.split(record.run_args))
                    p = subprocess.Popen(
                        cmd, cwd=tmp,
                        env=self._apply_portable_env(env or os.environ.copy(), app_dir, record),
                        start_new_session=True,
                    )
                    _track_detached(p)
                    self._temp_launch_dirs.append(tmp)
                    if not hasattr(self, '_temp_atexit_registered'):
                        atexit.register(self._cleanup_temp_dirs)
                        self._temp_atexit_registered = True
                    self._status_bar.showMessage(f"Running {app_name} (extracted mode)")
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
                self, "Security Error",
                f"Refusing to uninstall: the path '{real}' is not in a managed directory.\n\n"
                f"Uninstall is only allowed for paths under ~/Applications or ~/.local.",
            )
            return

        wizard = UninstallWizard(app_name, app_dir, self)
        if wizard.exec() == QDialog.DialogCode.Accepted:
            self.scan_installed()
            self._status_bar.showMessage(f"Uninstalled {app_name}")

    def _update_app(self, app_name: str):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            f"Select new AppImage for {app_name}",
            os.path.expanduser("~"),
            "AppImage files (*.AppImage);;All files (*)",
        )
        if not file_path:
            return

        old_dir = self.installed_apps[app_name]["path"]

        # Create a previous-version backup for rollback
        prev_dir = old_dir + ".prev"
        if os.path.exists(prev_dir):
            shutil.rmtree(prev_dir)
        self._create_prev_backup(old_dir, prev_dir)

        backup_dir = old_dir + ".backup"
        if os.path.exists(backup_dir):
            shutil.rmtree(backup_dir)

        if os.path.isdir(old_dir):
            try:
                shutil.copytree(old_dir, backup_dir)
            except OSError:
                backup_dir = None
                self._status_bar.showMessage("Warning: could not create backup before update")

        dest_dir = old_dir

        progress = QProgressDialog(f"Updating {app_name}...", "Cancel", 0, 0, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setCancelButton(None)
        progress.show()

        self.worker = ExtractionWorker(file_path, dest_dir, app_name, self)
        self.worker.extraction_finished.connect(
            lambda d, n: self._on_update_finished(d, n, progress, backup_dir, prev_dir)
        )
        self.worker.extraction_error.connect(
            lambda e: self._on_update_error(e, progress, backup_dir, dest_dir)
        )
        self.worker.start()

    def _create_prev_backup(self, app_dir: str, prev_dir: str):
        """Create a .prev backup of the current version for rollback."""
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
        except OSError as e:
            logging.warning("Could not create rollback backup: %s", e)

    def _revert_app(self, app_name: str):
        """Revert to the .prev version if it exists."""
        app_info = self.installed_apps.get(app_name)
        if not app_info:
            return
        app_dir = app_info["path"]
        prev_dir = app_dir + ".prev"
        if not os.path.isdir(prev_dir):
            QMessageBox.information(self, "No Backup",
                                    f"No previous version found for {app_name}.")
            return
        reply = QMessageBox.question(
            self, "Revert Version",
            f"Revert {app_name} to the previous version?\n"
            "The current version will be moved to a backup.",
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
            self.scan_installed()
            self._status_bar.showMessage(f"Reverted {app_name} to previous version")
        except OSError as e:
            play_sound("error")
            QMessageBox.critical(self, "Revert Failed", str(e))

    def _on_update_finished(self, dest_dir: str, app_name: str,
                            progress: QProgressDialog, backup_dir: str | None,
                            prev_dir: str | None = None):
        progress.close()
        if prev_dir and os.path.isdir(prev_dir):
            # Keep .prev for rollback
            pass
        if backup_dir and os.path.isdir(backup_dir):
            try:
                shutil.rmtree(backup_dir)
            except OSError:
                pass
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
                self._status_bar.showMessage(f"Warning: could not create desktop entry: {e}")

        registry = InstallationRegistry()
        record = registry.get(app_name)
        if record:
            record.version = version
            registry.add(record)

        self.scan_installed()
        self._status_bar.showMessage(f"Updated {app_name}")

    def _on_update_error(self, error_msg: str, progress: QProgressDialog, backup_dir: str | None, dest_dir: str):
        play_sound("error")
        progress.close()
        if backup_dir and os.path.isdir(backup_dir):
            try:
                if os.path.exists(dest_dir):
                    shutil.rmtree(dest_dir)
                shutil.copytree(backup_dir, dest_dir, dirs_exist_ok=True)
                shutil.rmtree(backup_dir)
            except OSError as e:
                QMessageBox.critical(
                    self, "Restore Failed",
                    f"Update failed and backup could not be restored: {e}\n\n"
                    f"Your data is at: {backup_dir}"
                )
                return
            QMessageBox.critical(
                self, "Update Error",
                f"Failed to update: {error_msg}\n\nPrevious version restored."
            )
        else:
            QMessageBox.critical(self, "Update Error", f"Failed to update: {error_msg}")

    def _create_desktop_shortcut(self, app_name: str):
        app_info = self.installed_apps[app_name]
        apprun = os.path.join(app_info["path"], "AppRun")
        icon_path = app_info.get("icon_path")
        shortcut_path = create_desktop_shortcut(app_name, apprun, icon_path)
        if shortcut_path:
            self._status_bar.showMessage(f"Desktop shortcut created for {app_name}")
            app_info["desktop_shortcut"] = shortcut_path
        else:
            play_sound("warning")
            QMessageBox.warning(self, "Error", "Failed to create desktop shortcut.")

    def _remove_desktop_shortcut(self, app_name: str):
        shortcut = find_desktop_shortcut(app_name)
        if shortcut and os.path.exists(shortcut):
            os.remove(shortcut)
            self.installed_apps[app_name]["desktop_shortcut"] = None
            self._status_bar.showMessage(f"Desktop shortcut removed for {app_name}")

    def _open_folder(self, app_name: str):
        app_dir = self.installed_apps[app_name]["path"]
        try:
            p = subprocess.Popen(["xdg-open", app_dir], start_new_session=True)
            _track_detached(p)
        except OSError:
            play_sound("error")
            QMessageBox.critical(self, "Error", "Could not open folder.")

    def _open_app_hooks(self, app_name: str):
        from niruvi.hooks import ensure_hooks_dir
        hooks_dir = ensure_hooks_dir(app_name)
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
        from niruvi.health_check import check_app_runnable, check_fuse_available
        from niruvi.installation_registry import InstallationRegistry
        registry = InstallationRegistry()
        record = registry.get(app_name)

        health = check_app_health(app_name, app_dir, record)
        runnable = check_app_runnable(app_name, app_dir)
        fuse = check_fuse_available()

        has_real_issues = bool(health["issues"]) or bool(runnable["issues"])
        has_info_warnings = bool(health["warnings"]) or bool(runnable["warnings"])

        pal = self.palette()
        is_dark = pal.window().color().lightness() < 128
        if is_dark:
            error_bg = "#3d1a1a"
            error_border = "#993333"
            warn_bg = "#3d3500"
            warn_border = "#997a00"
            ok_color = "#66cc66"
        else:
            error_bg = "#fdd"
            error_border = "#f99"
            warn_bg = "#fff3cd"
            warn_border = "#ffc107"
            ok_color = "green"
        disabled_hex = pal.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text).name()
        mid_hex = pal.mid().color().name()

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
            icon_lbl.setPixmap(get_icon("emblem-important", "dialog-warning").pixmap(32, 32))
            icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(icon_lbl)

        if health["issues"]:
            lbl = QLabel("<b style='color:red;'>Issues:</b><br>" + "<br>".join(f"• {i}" for i in health["issues"]))
            lbl.setWordWrap(True)
            lbl.setStyleSheet(f"background:{error_bg};border:1px solid {error_border};border-radius:4px;padding:8px;")
            layout.addWidget(lbl)

        if runnable["issues"]:
            lbl = QLabel("<b style='color:red;'>Pre-launch issues:</b><br>" + "<br>".join(f"• {i}" for i in runnable["issues"]))
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
            info_lines.append(f"<span style='color:{disabled_hex};font-size:0.85em;'>Note: Some libraries may load at runtime. This check may show false positives.</span>")
        if health.get("info", {}).get("no_update_url"):
            info_lines.append("<b>Update URL:</b> Not configured (optional)")
        if runnable.get("info", {}).get("apprun_size"):
            s = runnable["info"]["apprun_size"]
            info_lines.append(f"<b>AppRun size:</b> {s / 1024:.0f} KB")

        if info_lines:
            info_lbl = QLabel("<br>".join(info_lines))
            info_lbl.setWordWrap(True)
            info_lbl.setStyleSheet("background:palette(window);border:1px solid palette(mid);border-radius:4px;padding:8px;")
            layout.addWidget(info_lbl)

        if health["warnings"] or runnable["warnings"]:
            warn_text = f"<b style='color:{mid_hex};'>Notes:</b><br>"
            for w in health["warnings"] + runnable["warnings"]:
                warn_text += f"• {w}<br>"
            warn_lbl = QLabel(warn_text)
            warn_lbl.setWordWrap(True)
            warn_lbl.setStyleSheet(f"background:{warn_bg};border:1px solid {warn_border};border-radius:4px;padding:8px;")
            layout.addWidget(warn_lbl)

        if not has_real_issues:
            ok_lbl = QLabel(f"<span style='color:{ok_color};font-size:14px;'>App appears healthy and runnable.</span>")
            ok_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(ok_lbl)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        run_btn = QPushButton(get_icon("media-playback-start"), "Run App")
        run_btn.clicked.connect(lambda: (dlg.accept(), self._run_app(app_name)))
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
        dlg = AppInfoDialog(app_name, app_info, self)
        dlg.exec()
        self.scan_installed()

    def _check_all_app_updates(self):
        registry = InstallationRegistry()
        records = registry.get_all()
        apps_with_url = [(r.name, r.update_url, r.version, r.update_channel) for r in records if r.update_url]
        if not apps_with_url:
            QMessageBox.information(
                self, "No Update URLs",
                "No installed apps have an update URL configured.\n\n"
                "Open each app's info page to set its update URL."
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
                from niruvi.self_update import compare_versions
                if compare_versions(info.version, 'gt', current_version):
                    reply = QMessageBox.question(
                        self, f"Update Available: {name}",
                        f"Version {info.version} is available for {name}.\n"
                        f"Current: {current_version}\n"
                        f"Source: {info.source_type}\n\n"
                        + (f"What's new:\n{info.changelog[:300]}\n\n" if info.changelog else "") +
                        "Download and install now?",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    )
                    if reply == QMessageBox.StandardButton.Yes:
                        self._download_app_update(name, info.download_url, info.version)
                        updated += 1
                    else:
                        failed += 1
                else:
                    up_to_date += 1
            except Exception as e:
                self._status_bar.showMessage(f"Update check failed for {name}: {e}")
        if updated == 0 and failed == 0 and up_to_date > 0:
            QMessageBox.information(self, "All Up to Date", "All configured apps are already up to date.")
        elif updated > 0:
            self.scan_installed()
            self._status_bar.showMessage(f"Updated {updated} app(s)")

    def _check_single_app_update(self, app_name: str, update_url: str, current_version: str):
        if not update_url:
            QMessageBox.information(
                self, "No Update URL",
                f"No update URL configured for {app_name}.\n\n"
                "Open App Info to set one.",
            )
            return
        if not current_version or current_version == "unknown":
            QMessageBox.information(
                self, "Unknown Version",
                f"The current version of {app_name} is unknown. Update check cannot proceed."
            )
            return

        # First try zsync delta update if available
        app_info = self.installed_apps.get(app_name)
        if app_info:
            app_dir = app_info.get("path", "")
            if app_dir:
                from niruvi.appimageupdate import get_update_method_for_app
                method_info = get_update_method_for_app(app_dir)
                if method_info["method"] == "zsync":
                    ret = QMessageBox.question(
                        self, "Delta Update Available",
                        f"An AppImageUpdate-compatible update is available for <b>{app_name}</b>.<br><br>"
                        f"Update info: <code>{method_info['update_info'][:60]}</code><br><br>"
                        "Use delta update (smaller download)?",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    )
                    if ret == QMessageBox.StandardButton.Yes:
                        self._zsync_update_app(app_name, app_dir)
                        return
                    # Falls through to full download

        try:
            registry = InstallationRegistry()
            record = registry.get(app_name)
            channel = record.update_channel if record else "stable"
            info = resolve_update_source(update_url, current_version, channel=channel)
            if not info or not info.version:
                play_sound("warning")
                QMessageBox.warning(self, "Update Check Failed", f"Could not resolve update source for {app_name}.")
                return
            from niruvi.self_update import compare_versions
            if compare_versions(info.version, 'gt', current_version):
                msg = f"Version {info.version} is available for {app_name}.\nCurrent: {current_version}\n"
                if info.changelog:
                    msg += f"\nWhat's new:\n{info.changelog[:1000]}"
                if info.sha256:
                    msg += f"\n\nSHA256: {info.sha256[:16]}..."
                dlg = QMessageBox(self)
                dlg.setWindowTitle(f"Update Available: {app_name}")
                dlg.setText(msg)
                dlg.setDetailedText(
                    f"Source: {info.source_type}\n"
                    f"Channel: {channel}\n"
                    f"Download URL: {info.download_url}\n"
                    + (f"SHA256: {info.sha256}\n" if info.sha256 else "")
                )
                dlg.setStandardButtons(
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                dlg.setDefaultButton(QMessageBox.StandardButton.Yes)
                dlg.button(QMessageBox.StandardButton.Yes).setText("Download & Install")
                reply = dlg.exec()
                if reply == QMessageBox.StandardButton.Yes:
                    self._download_app_update(app_name, info.download_url, info.version, info.sha256)
            else:
                QMessageBox.information(
                    self, "Up to Date",
                    f"{app_name} (version {current_version}) is already the latest version."
                )
        except Exception as e:
            play_sound("warning")
            QMessageBox.warning(self, "Update Check Failed", str(e))

    def _download_app_update(self, app_name: str, download_url: str,
                              latest_version: str, expected_sha256: str = ""):
        from niruvi.worker import DownloadWorker, extract_appimage_sync
        app_info = self.installed_apps.get(app_name)
        if app_info is None:
            return
        dest_dir = app_info["path"]

        fd, temp_path = tempfile.mkstemp(suffix=".AppImage")
        os.close(fd)

        progress = QProgressDialog(f"Downloading {app_name} update...", "Cancel", 0, 100, self)
        progress.setWindowTitle("Downloading Update")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setAutoClose(True)
        progress.setValue(0)

        worker = DownloadWorker(download_url, temp_path, expected_sha256, self)
        worker.progress_updated.connect(progress.setValue)
        loop = QEventLoop()
        result = [None]
        error_msg = [None]

        def on_finished(p):
            result[0] = p
            loop.quit()

        def on_error(e):
            error_msg[0] = e
            loop.quit()

        worker.finished.connect(on_finished)
        worker.error.connect(on_error)
        worker.start()
        progress.canceled.connect(worker.cancel)
        loop.exec()
        progress.close()

        if error_msg[0]:
            play_sound("error")
            Path(temp_path).unlink(missing_ok=True)
            QMessageBox.critical(self, "Download Failed", str(error_msg[0]))
            return

        if progress.wasCanceled():
            Path(temp_path).unlink(missing_ok=True)
            return

        backup_dir = dest_dir + ".backup"
        if os.path.exists(dest_dir):
            shutil.copytree(dest_dir, backup_dir, dirs_exist_ok=True)
        try:
            extract_appimage_sync(temp_path, dest_dir)
        except Exception as e:
            if os.path.isdir(backup_dir):
                shutil.rmtree(dest_dir, ignore_errors=True)
                os.replace(backup_dir, dest_dir)
            Path(temp_path).unlink(missing_ok=True)
            play_sound("error")
            QMessageBox.critical(self, "Update Failed", f"Failed to extract update:\n{e}")
            return
        Path(temp_path).unlink(missing_ok=True)
        if os.path.isdir(backup_dir):
            shutil.rmtree(backup_dir, ignore_errors=True)
        from niruvi.desktop_utils import get_version
        version = get_version(dest_dir) or latest_version
        registry = InstallationRegistry()
        record = registry.get(app_name)
        if record:
            record.version = version
            registry.add(record)
        self._status_bar.showMessage(f"Updated {app_name} to version {version}")

    def _zsync_update_app(self, app_name: str, app_dir: str):
        """Update an app using AppImageUpdate (zsync delta update)."""
        from niruvi.appimageupdate import (
            find_appimage_in_dir, update_appimage_via_tool,
            get_update_info_from_appimage,
        )

        appimage_path = find_appimage_in_dir(app_dir)
        if not appimage_path:
            QMessageBox.information(
                self, "Original AppImage Not Found",
                f"The original AppImage file was not found in {app_dir}.\n\n"
                "Falling back to full download update.",
            )
            self._check_single_app_update(
                app_name,
                InstallationRegistry().get(app_name).update_url,
                "",
            )
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
                    self, "Delta Update Failed",
                    f"AppImageUpdate failed for {app_name}:<br><br><code>{msg}</code><br><br>"
                    "Falling back to full download.",
                )
                self._check_single_app_update(
                    app_name,
                    InstallationRegistry().get(app_name).update_url,
                    "",
                )
                return

            # Create .prev backup for rollback
            prev_dir = app_dir + ".prev"
            if os.path.exists(prev_dir):
                shutil.rmtree(prev_dir)
            if os.path.isdir(app_dir):
                self._create_prev_backup(app_dir, prev_dir)

            # Re-extract the updated AppImage
            backup_dir = app_dir + ".bak"
            if os.path.isdir(app_dir):
                shutil.copytree(app_dir, backup_dir, ignore_errors=True)

            from niruvi.worker import extract_appimage_sync
            extract_appimage_sync(appimage_path, app_dir)

            if os.path.isdir(backup_dir):
                shutil.rmtree(backup_dir, ignore_errors=True)

            from niruvi.desktop_utils import get_version
            version = get_version(app_dir) or ""
            registry = InstallationRegistry()
            record = registry.get(app_name)
            if record:
                record.version = version
                registry.add(record)

            self._status_bar.showMessage(f"Updated {app_name} via delta update (version {version})")
            QMessageBox.information(
                self, "Update Complete",
                f"<b>{app_name}</b> was updated via delta update.<br><br>"
                f"Result: {msg}",
            )

        except Exception as e:
            progress.close()
            play_sound("error")
            QMessageBox.critical(self, "Update Failed", f"Delta update failed:\n{e}")

    def _open_build_dialog(self):
        dialog = BuildDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._status_bar.showMessage("AppImage built successfully!")
        else:
            self._status_bar.showMessage("Build cancelled or failed")

    def _check_all_updates(self):
        check_for_updates(self)

    def _start_background_updater(self):
        interval = _settings.get("update_check_interval", "weekly")
        from niruvi.background_updater import INTERVAL_OPTIONS
        seconds = INTERVAL_OPTIONS.get(interval, 604800)
        auto_update = _settings.get("auto_update_apps", False)
        if seconds > 0:
            self._background_updater.start(seconds, auto_update)

    def _on_background_update_found(self, result):
        play_sound("notification")
        from PyQt6.QtWidgets import QSystemTrayIcon, QMenu
        app = QApplication.instance()
        has_tray = app and hasattr(app, "desktop") and QSystemTrayIcon.isSystemTrayAvailable()
        if has_tray:
            try:
                tray = QSystemTrayIcon(
                    get_icon("emblem-downloads", "emblem-important"),
                    app.activeWindow() or self,
                )
                tray.setToolTip(f"Update available for {result.app_name}")
                menu = QMenu()
                show_action = menu.addAction(f"Show {result.app_name} update")
                show_action.triggered.connect(
                    lambda: self._show_app_update_notification(result)
                )
                tray.setContextMenu(menu)
                tray.show()
                tray.showMessage(
                    "Update Available",
                    f"{result.app_name} v{result.latest_version} is available",
                    QSystemTrayIcon.MessageIcon.Information,
                    5000,
                )
                return
            except Exception:
                pass
        reply = QMessageBox.question(
            self, "Update Available",
            f"{result.app_name} v{result.latest_version} is available "
            f"(current: v{result.current_version}).\n\n"
            f"Source: {result.source_type}\n"
            + (f"\n{result.changelog[:300]}" if result.changelog else "") +
            "\n\nDownload and install now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._download_app_update(result.app_name, result.download_url, result.latest_version)

    def _show_app_update_notification(self, result):
        app_info = self.installed_apps.get(result.app_name)
        if app_info:
            self._show_app_info(result.app_name)

    def _export_app_list(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export App List", "niruvi-apps.json",
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
            self._status_bar.showMessage(f"Exported {len(data)} app(s) to {file_path}")
        except OSError as e:
            play_sound("error")
            QMessageBox.critical(self, "Export Failed", str(e))

    def _import_app_list(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Import App List", "",
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
        self._status_bar.showMessage(
            f"Imported {imported} app(s)" + (f", skipped {skipped}" if skipped else "")
        )

    def open_settings(self):
        dialog = SettingsDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.scan_installed()

    def _show_help(self):
        dlg = HelpDialog(self)
        dlg.exec()

    def _show_device_info(self):
        dlg = DeviceInfoDialog(self)
        dlg.exec()

    def _show_report_page(self):
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QDialogButtonBox
        dlg = QDialog(self)
        dlg.setWindowTitle("Report Issue")
        dlg.setMinimumSize(520, 400)
        layout = QVBoxLayout(dlg)
        layout.addWidget(ReportPage(dlg))
        btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn.rejected.connect(dlg.accept)
        layout.addWidget(btn)
        dlg.exec()

    def _show_license(self):
        dlg = HelpDialog(self, initial_page="License")
        dlg.exec()

    def _show_about(self):
        from niruvi._version import __version__
        QMessageBox.about(
            self,
            "About Niruvi",
            f"<b>Niruvi</b> v{__version__}<br><br>"
            "A universal Linux AppImage manager for installing,<br>"
            "updating, uninstalling, and building AppImages.<br><br>"
            "<b>Features:</b><br>"
            "• Install, update, and manage AppImages<br>"
            "• Build AppImages from DEB, RPM, and tar packages<br>"
            "• Create desktop entries and shortcuts<br>"
            "• Security scanner with malware detection<br>"
            "• Backup/rollback on update or overwrite<br>"
            "• Portable home &amp; config folder support<br>"
            "• Drag-and-drop install<br>"
            "• Search and filter installed apps<br>"
            "• Cross-desktop icon theme integration<br><br>"
            "<b>License:</b> GNU General Public License v3<br>"
            "Copyright © 2026 putinservai-cyber<br><br>"
            "This program is free software; you can redistribute it<br>"
            "and/or modify it under the terms of the GNU General<br>"
            "Public License as published by the Free Software<br>"
            "Foundation; version 3 of the License."
        )
