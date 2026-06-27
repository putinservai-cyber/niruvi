import json
import os
import shutil
import subprocess
import tempfile
import urllib.request

import logging

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem,
    QMessageBox, QMenu, QFileDialog, QProgressDialog,
    QStatusBar, QDialog, QLabel, QPushButton,
    QLineEdit, QComboBox, QFrame, QSizePolicy,
    QApplication,
)
from PyQt6.QtCore import Qt, QSize, QThread, QEventLoop, pyqtSignal
from PyQt6.QtGui import QAction, QPixmap, QIcon, QDragEnterEvent, QDropEvent
from pathlib import Path

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
    refresh_desktop_database,
    parse_desktop_file,
)
from niruvi.wizard import InstallWizard
from niruvi.build_dialog import BuildDialog
from niruvi.help_dialog import HelpDialog, LicenseDialog
from niruvi.uninstall_dialog import UninstallWizard
from niruvi.report_page import ReportPage
from niruvi.utils import get_icon
from niruvi.scanner import scan_appimage, self_scan
from niruvi.hooks import run_hooks, ensure_hooks_dir, list_hooks
from niruvi.health_check import check_app_health, format_health_icon, get_health_summary
from niruvi.installation_registry import InstallationRegistry
from niruvi.icon_utils import get_pixmap_from_file, get_pixmap_from_data, to_png_bytes
from niruvi.appimage_metadata import AppImageMetadata
from niruvi.appimage_assets import extract_metadata
from niruvi.self_update import check_for_updates
from niruvi.background_updater import BackgroundUpdater
from niruvi.update_sources import resolve_update_source
from niruvi.installation_registry import InstallationRecord
from niruvi.settings import _settings

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


class MetadataWorker(QThread):
    finished = pyqtSignal(dict, object)

    def __init__(self, appimage_path, parent=None):
        super().__init__(parent)
        self.appimage_path = appimage_path

    def run(self):
        info, icon_data = get_appimage_metadata(self.appimage_path)
        self.finished.emit(info, icon_data)


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
        self._init_ui()
        self.scan_installed()
        self._start_background_updater()

    def _init_ui(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu(get_icon("document-open"), "File")

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

        build_action = QAction(get_icon("emblem-system", "applications-utilities", "document-export"), "Build AppImage...", self)
        build_action.triggered.connect(self._open_build_dialog)
        tools_menu.addAction(build_action)

        scan_action = QAction(get_icon("bug", "tools-report-bug", "dialog-warning"), "Scan AppImage...", self)
        scan_action.triggered.connect(self._scan_appimage_dialog)
        tools_menu.addAction(scan_action)

        health_action = QAction(get_icon("emblem-important", "dialog-warning"), "Health Check...", self)
        health_action.triggered.connect(self._show_health_report)
        tools_menu.addAction(health_action)

        tools_menu.addSeparator()

        check_updates_action = QAction(get_icon("emblem-downloads", "download", "document-save"), "Check for Niruvi Updates...", self)
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

        self_check_action = QAction(
            get_icon("security-high", "dialog-password", "emblem-ok"), "Security Self-Check", self
        )
        self_check_action.setToolTip("Run a security scan on Niruvi itself")
        self_check_action.triggered.connect(self._run_self_security_check)
        help_menu.addAction(self_check_action)

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
        self.drop_hint = QLabel(
            '<div style="text-align: center; padding: 40px;">'
            '<span style="font-size: 32px;">📦</span><br><br>'
            '<b style="font-size: 16px;">Drop AppImage files here</b><br>'
            '<span style="color: #888;">to install them automatically</span>'
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
        super().closeEvent(event)

    def _add_app_to_list(self, key: str, app_dir: str, version: str, display_name: str | None = None, icon_path: str | None = None, update_url: str = "", architecture: str = "", display_name_override: str = "", custom_icon_path: str = "", health_info: dict | None = None):
        if display_name is None:
            display_name = key
        version_str = version if version and version != "unknown" else ""
        text = f"{display_name}" + (f"  (v{version_str})" if version_str else "")
        list_item = QListWidgetItem(text)
        list_item.setData(Qt.ItemDataRole.UserRole, key)
        tooltip = (
            f"Path: {app_dir}\n"
            f"Version: {version_str or 'unknown'}\n"
            f"Name: {key}"
        )
        if health_info:
            if health_info.get("issues"):
                tooltip += f"\n⚠ Issues: {'; '.join(health_info['issues'])}"
            if health_info.get("warnings"):
                tooltip += f"\n⚡ Warnings: {'; '.join(health_info['warnings'])}"
        list_item.setToolTip(tooltip)
        if icon_path:
            pixmap = get_pixmap_from_file(icon_path, 32)
            if pixmap and not pixmap.isNull():
                list_item.setIcon(QIcon(pixmap))
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
        self.installed_apps.clear()
        self.installed_list.clear()
        install_dir = get_settings().get("install_dir", DEFAULT_INSTALL_DIR)
        seen = set()
        registry = InstallationRegistry()

        if os.path.isdir(install_dir):
            for item in sorted(os.listdir(install_dir)):
                app_dir = os.path.join(install_dir, item)
                if not os.path.isdir(app_dir):
                    continue
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
            items.sort(key=lambda x: x[0].lower())
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
        for ext in (".png", ".svg", ".xpm"):
            for root, _, files in os.walk(app_dir):
                for f in files:
                    if f.endswith(ext):
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
        for url in event.mimeData().urls():
            if url.isLocalFile() and url.path().lower().endswith('.appimage'):
                self.process_appimage(url.path())
                break

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

        self._metadata_worker.finished.connect(on_finished)
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
            dlg.setFixedSize(420, 240)
            dlg.setModal(True)
            layout = QVBoxLayout(dlg)
            layout.setContentsMargins(24, 24, 24, 24)
            layout.setSpacing(12)

            title = QLabel(f"<b>{app_name}</b> is already installed")
            title.setWordWrap(True)
            layout.addWidget(title)
            desc = QLabel("What would you like to do?")
            layout.addWidget(desc)
            layout.addStretch()

            btn_row = QHBoxLayout()
            btn_row.setSpacing(10)

            btn_reinstall = QPushButton(get_icon("view-refresh"), "Re-integrate")
            btn_reinstall.clicked.connect(lambda: dlg.done(1))
            btn_row.addWidget(btn_reinstall)

            btn_sbs = QPushButton(get_icon("list-add"), "Install Side-by-Side")
            btn_sbs.setToolTip("Install as a separate version alongside the existing one")
            btn_sbs.clicked.connect(lambda: dlg.done(3))
            btn_row.addWidget(btn_sbs)

            btn_remove = QPushButton(get_icon("edit-delete"), "Remove & Install")
            btn_remove.clicked.connect(lambda: dlg.done(2))
            btn_row.addWidget(btn_remove)

            btn_cancel = QPushButton(get_icon("dialog-cancel"), "Cancel")
            btn_cancel.clicked.connect(lambda: dlg.done(0))
            btn_row.addWidget(btn_cancel)

            layout.addLayout(btn_row)
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
        menu = QMenu()
        info_action = menu.addAction(get_icon("help-about", "dialog-information"), "App Info")
        menu.addSeparator()
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
        hooks_action = menu.addAction(get_icon("folder-open"), "Edit Hooks for This App")
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
        if app_name:
            self._run_app(app_name)

    def _run_app(self, app_name: str):
        app_info = self.installed_apps[app_name]
        app_dir = app_info["path"]
        apprun = os.path.join(app_dir, "AppRun")
        if not os.path.isfile(apprun):
            expected = os.path.join(get_settings()["install_dir"], app_name, "AppRun")
            if os.path.isfile(expected):
                apprun = expected
                app_dir = os.path.dirname(expected)
            else:
                self._run_app_fallback(app_name, app_dir)
                return
        registry = InstallationRegistry()
        record = registry.get(app_name)
        env = os.environ.copy()
        if record and record.env_vars:
            env.update(record.env_vars)
        hook_results = run_hooks(app_name, app_dir, env)
        for hr in hook_results:
            if hr["returncode"] != 0:
                logging.warning("Hook %s failed: %s", hr["hook"], hr["stderr"])
        cmd = [apprun]
        if record and record.run_args:
            import shlex
            cmd.extend(shlex.split(record.run_args))
        try:
            p = subprocess.Popen(
                cmd,
                cwd=app_dir,
                env=env,
                start_new_session=True,
            )
            _track_detached(p)
            self._status_bar.showMessage(f"Running {app_name}")
        except OSError as e:
            self._run_app_fallback(app_name, app_dir, env, record)

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
        QMessageBox.critical(
            self, "Run Error",
            f"Could not run '{app_name}'.<br><br>"
            "Failed to run via FUSE, namespace, or extraction.",
        )

    def _find_appimage_in_dir(self, app_dir: str) -> str | None:
        for f in os.listdir(app_dir):
            if f.endswith(".AppImage") and os.path.isfile(os.path.join(app_dir, f)):
                return os.path.join(app_dir, f)
        parent = os.path.dirname(app_dir)
        for f in os.listdir(parent):
            if f.lower().startswith(app_dir.lower().replace(" ", "")) and f.endswith(".AppImage"):
                return os.path.join(parent, f)
        return None

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
                env=env or os.environ.copy(),
                start_new_session=True,
            )
            _track_detached(p)
            self._status_bar.showMessage(f"Running {app_name} (namespace mode)")
            return True
        except OSError:
            return False

    def _try_extract_and_run_temp(self, app_dir: str, app_name: str,
                                   env: dict | None = None, record=None) -> bool:
        import tempfile
        import shutil
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
                        env=env or os.environ.copy(),
                        start_new_session=True,
                    )
                    _track_detached(p)
                    self._status_bar.showMessage(f"Running {app_name} (extracted mode)")
                    return True
        except Exception as e:
            logging.error("Temp extraction failed for %s: %s", app_name, e)
        finally:
            try:
                shutil.rmtree(tmp, ignore_errors=True)
            except OSError:
                pass
        return False

    def _uninstall_app(self, app_name: str):
        app_info = self.installed_apps[app_name]
        app_dir = app_info["path"]
        if not os.path.isdir(app_dir):
            expected = os.path.join(get_settings()["install_dir"], app_name)
            if os.path.isdir(expected):
                app_dir = expected

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
            lambda d, n: self._on_update_finished(d, n, progress, backup_dir)
        )
        self.worker.extraction_error.connect(
            lambda e: self._on_update_error(e, progress, backup_dir, dest_dir)
        )
        self.worker.start()

    def _on_update_finished(self, dest_dir: str, app_name: str, progress: QProgressDialog, backup_dir: str | None):
        progress.close()
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
            QMessageBox.critical(self, "Error", "Could not open folder.")

    def _open_app_hooks(self, app_name: str):
        from niruvi.hooks import ensure_hooks_dir
        hooks_dir = ensure_hooks_dir(app_name)
        try:
            p = subprocess.Popen(["xdg-open", hooks_dir], start_new_session=True)
            _track_detached(p)
        except OSError:
            QMessageBox.critical(self, "Error", "Could not open hooks directory.")

    def _show_app_info(self, app_name: str):
        app_info = self.installed_apps.get(app_name)
        if not app_info:
            return
        dlg = AppInfoDialog(app_name, app_info, self)
        dlg.exec()
        self.scan_installed()

    def _show_health_report(self):
        registry = InstallationRegistry()
        summary = get_health_summary(self.installed_apps, registry)
        lines = [
            "<b>Health Report</b><br><br>",
            f"<b>FUSE available:</b> {'Yes' if summary['fuse_available'] else 'No'}<br>",
            f"<b>Namespaces available:</b> {'Yes' if summary['namespace_available'] else 'No'}<br><br>",
            f"<b>Total apps:</b> {summary['total']}<br>",
            f"<b>Healthy:</b> {summary['healthy']}<br>",
            f"<b>With issues:</b> {summary['issues']}<br>",
            f"<b>With warnings:</b> {summary['warnings']}<br><br>",
        ]
        if summary["issues"]:
            lines.append("<b>Apps with issues:</b><br>")
            for name, h in summary["results"].items():
                if h["issues"]:
                    lines.append(f"• <b>{name}</b>: {'; '.join(h['issues'])}<br>")
            lines.append("<br>")
        if summary["warnings"]:
            lines.append("<b>Apps with warnings:</b><br>")
            for name, h in summary["results"].items():
                if h["warnings"]:
                    lines.append(f"• <b>{name}</b>: {'; '.join(h['warnings'])}<br>")

        msg = QMessageBox(self)
        msg.setWindowTitle("Health Check")
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setText("".join(lines))
        msg.setIcon(QMessageBox.Icon.Information)
        if summary["issues"]:
            msg.setIcon(QMessageBox.Icon.Warning)
        msg.exec()

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
        try:
            info = resolve_update_source(update_url, current_version)
            if not info or not info.version:
                QMessageBox.warning(self, "Update Check Failed", f"Could not resolve update source for {app_name}.")
                return
            from niruvi.self_update import compare_versions
            if compare_versions(info.version, 'gt', current_version):
                msg = f"Version {info.version} is available for {app_name}.\nCurrent: {current_version}\n"
                if info.changelog:
                    msg += f"\nWhat's new:\n{info.changelog[:500]}"
                reply = QMessageBox.question(
                    self, f"Update Available: {app_name}", msg + "\n\nDownload and install now?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self._download_app_update(app_name, info.download_url, info.version)
            else:
                QMessageBox.information(
                    self, "Up to Date",
                    f"{app_name} (version {current_version}) is already the latest version."
                )
        except Exception as e:
            QMessageBox.warning(self, "Update Check Failed", str(e))

    def _download_app_update(self, app_name: str, download_url: str, latest_version: str):
        from niruvi.app_info_dialog import AppInfoDialog
        progress = QProgressDialog(f"Downloading {app_name} update...", "Cancel", 0, 100, self)
        progress.setWindowTitle("Downloading Update")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setAutoClose(True)
        progress.setValue(0)
        temp_path = None
        dest_dir = self.installed_apps[app_name]["path"]
        try:
            fd, temp_path = tempfile.mkstemp(suffix=".AppImage")
            os.close(fd)
            resp = urllib.request.urlopen(download_url, timeout=120)
            total = int(resp.headers.get("Content-Length", 0))
            chunk_size = 8192
            downloaded = 0
            with open(temp_path, "wb") as f:
                while True:
                    if progress.wasCanceled():
                        Path(temp_path).unlink(missing_ok=True)
                        return
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        progress.setValue(int((downloaded / total) * 100))
            progress.close()
            backup_dir = dest_dir + ".backup"
            if os.path.exists(dest_dir):
                shutil.copytree(dest_dir, backup_dir, dirs_exist_ok=True)
            from niruvi.worker import extract_appimage_sync
            extract_appimage_sync(temp_path, dest_dir)
            if os.path.exists(backup_dir):
                shutil.rmtree(backup_dir, ignore_errors=True)
            from niruvi.desktop_utils import get_version
            version = get_version(dest_dir) or latest_version
            registry = InstallationRegistry()
            record = registry.get(app_name)
            if record:
                record.version = version
                registry.add(record)
            self._status_bar.showMessage(f"Updated {app_name} to version {version}")
        except Exception as e:
            progress.close()
            QMessageBox.critical(self, "Update Failed", f"Failed to update {app_name}:\n{e}")

    def _open_build_dialog(self):
        dialog = BuildDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._status_bar.showMessage("AppImage built successfully!")
        else:
            self._status_bar.showMessage("Build cancelled or failed")

    def _scan_appimage_dialog(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select AppImage to Scan", "",
            "AppImage files (*.AppImage);;All files (*)",
        )
        if not file_path:
            return
        progress = QProgressDialog(f"Scanning {Path(file_path).name}...", None, 0, 0, self)
        progress.setWindowTitle("Security Scan")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()
        QApplication.processEvents()
        try:
            result = scan_appimage(file_path)
        except Exception as e:
            progress.close()
            QMessageBox.critical(self, "Scan Failed", f"Could not scan file:\n{e}")
            return
        progress.close()
        self._show_scan_result(file_path, result)

    def _show_scan_result(self, file_path: str, result: dict):
        name = Path(file_path).name
        risk = result.get("risk_level", "unknown")
        warnings = result.get("warnings", [])
        sha256 = result.get("sha256", "?")[:16]
        size = result.get("size_mb", 0)

        color = {
            "safe": "green", "low": "orange", "medium": "orange", "high": "red",
        }.get(risk, "gray")
        icon_map = {
            "safe": get_icon("emblem-ok", "dialog-ok-apply"),
            "low": get_icon("dialog-warning"),
            "medium": get_icon("dialog-warning"),
            "high": get_icon("dialog-error", "dialog-cancel"),
        }
        scan_icon = icon_map.get(risk, get_icon("dialog-information"))

        msg = (
            f"<b>Scan Result: <span style='color:{color};'>{risk.upper()}</span></b><br><br>"
            f"<b>File:</b> {name}<br>"
            f"<b>Size:</b> {size:.1f} MB<br>"
            f"<b>SHA256:</b> {sha256}...<br>"
        )
        if warnings:
            msg += "<br><b>Warnings:</b><br>" + "<br>".join(f"• {w}" for w in warnings[:10])
        if risk == "safe":
            msg += "<br><i>No security issues detected.</i>"

        QMessageBox.information(self, "Security Scan Result", msg)

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
            QMessageBox.critical(self, "Import Failed", f"Could not read file:\n{e}")
            return
        if not isinstance(data, list):
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
        dlg = LicenseDialog(self)
        dlg.exec()

    def _run_self_security_check(self):
        from niruvi.scanner import self_scan
        result = self_scan()
        risk = result.get("risk_level", "unknown")
        warnings = result.get("warnings", [])
        appimage_path = result.get("details", {}).get("path", os.environ.get("APPIMAGE", ""))

        if not appimage_path:
            QMessageBox.information(
                self, "Security Self-Check",
                "<b>Not running from AppImage</b><br><br>"
                "Niruvi is running from a system install or source tree.<br>"
                "Self-scan is only available when running from an AppImage.<br><br>"
                "<i>No security issues to report.</i>"
            )
            return

        color = {"safe": "green", "low": "orange", "medium": "orange", "high": "red"}.get(risk, "gray")
        lines = [f"<b>Risk Level: <span style='color:{color};'>{risk.upper()}</span></b>"]
        lines.append(f"<br><b>File:</b> {os.path.basename(appimage_path)}")
        lines.append(f"<br><b>Size:</b> {result.get('size_mb', 0):.1f} MB")
        lines.append(f"<br><b>SHA256:</b> {result.get('sha256', '?')[:16]}...")
        if warnings:
            lines.append("<br><br><b>Warnings:</b><br>")
            lines.extend(f"• {w}" for w in warnings[:10])
        if risk in ("safe", "low"):
            lines.append("<br><br><span style='color:green;'>Niruvi AppImage passes security checks.</span>")
        else:
            lines.append("<br><br><span style='color:red;'>Issues found — consider downloading a fresh copy.</span>")

        if result.get("is_valid_appimage"):
            lines.append("<br><br><i>Valid AppImage signature confirmed.</i>")

        QMessageBox.information(self, "Security Self-Check", "".join(lines))

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
