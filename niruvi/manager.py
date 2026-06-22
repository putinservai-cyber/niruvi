import json
import os
import shutil
import struct
import subprocess
import tempfile

import logging

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem,
    QMessageBox, QMenu, QFileDialog, QProgressDialog,
    QStatusBar, QDialog, QLabel, QPushButton,
    QLineEdit, QComboBox, QFrame, QSizePolicy,
)
from PyQt6.QtCore import Qt, QSize, QThread, QEventLoop, pyqtSignal
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QAction, QPixmap, QIcon, QDragEnterEvent, QDropEvent
from pathlib import Path

from niruvi.settings import (
    get_settings,
    DEFAULT_INSTALL_DIR,
    SettingsDialog,
)
from niruvi.worker import ExtractionWorker
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
from niruvi.report_page import ReportPage
from niruvi.utils import get_icon
from niruvi.scanner import scan_appimage
from niruvi.installation_registry import InstallationRegistry
from niruvi.icon_utils import get_pixmap_from_file, get_pixmap_from_data, to_png_bytes
from niruvi.appimage_metadata import AppImageMetadata
from niruvi.appimage_assets import extract_metadata
from niruvi.self_update import check_for_updates


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
        self._init_ui()
        self.scan_installed()

    def _init_ui(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu(QIcon.fromTheme("document-open"), "File")

        install_action = QAction(QIcon.fromTheme("list-add"), "Install...", self)
        install_action.setShortcut("Ctrl+I")
        install_action.triggered.connect(self.run_install_wizard)
        file_menu.addAction(install_action)

        settings_action = QAction(QIcon.fromTheme("preferences-system"), "Settings...", self)
        settings_action.triggered.connect(self.open_settings)
        file_menu.addAction(settings_action)

        file_menu.addSeparator()

        quit_action = QAction(QIcon.fromTheme("application-exit"), "Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        tools_menu = menubar.addMenu(get_icon("applications-utilities", "preferences-other", "emblem-system"), "Tools")

        build_action = QAction(get_icon("emblem-system", "applications-utilities", "document-export"), "Build AppImage...", self)
        build_action.triggered.connect(self._open_build_dialog)
        tools_menu.addAction(build_action)

        tools_menu.addSeparator()

        check_updates_action = QAction(get_icon("emblem-downloads", "download", "document-save"), "Check for Updates...", self)
        check_updates_action.triggered.connect(lambda: check_for_updates(self))
        tools_menu.addAction(check_updates_action)

        refresh_action = QAction(QIcon.fromTheme("view-refresh"), "Refresh Installed", self)
        refresh_action.setShortcut("Ctrl+R")
        refresh_action.triggered.connect(self.scan_installed)
        tools_menu.addAction(refresh_action)

        help_menu = menubar.addMenu(QIcon.fromTheme("help-contents"), "Help")

        help_action = QAction(QIcon.fromTheme("help-contents"), "Help...", self)
        help_action.setShortcut("F1")
        help_action.triggered.connect(self._show_help)
        help_menu.addAction(help_action)

        report_action = QAction(
            get_icon("bug", "tools-report-bug", "dialog-warning"), "Report Issue...", self
        )
        report_action.triggered.connect(self._show_report_page)
        help_menu.addAction(report_action)

        license_action = QAction(get_icon("emblem-documents", "help-about", "document-properties"), "License", self)
        license_action.triggered.connect(self._show_license)
        help_menu.addAction(license_action)

        help_menu.addSeparator()

        about_action = QAction(QIcon.fromTheme("help-about"), "About", self)
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

        self.btn_build = QPushButton(QIcon.fromTheme("emblem-system"), "Build")
        self.btn_build.setToolTip("Build an AppImage from a DEB, RPM, or tar archive")
        self.btn_build.clicked.connect(self._open_build_dialog)
        header_layout.addWidget(self.btn_build)

        self.btn_refresh = QPushButton(QIcon.fromTheme("view-refresh"), "Refresh")
        self.btn_refresh.setToolTip("Re-scan the install directory for installed apps")
        self.btn_refresh.clicked.connect(self.scan_installed)
        header_layout.addWidget(self.btn_refresh)

        self.btn_install = QPushButton(QIcon.fromTheme("list-add"), "Install AppImage...")
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
        self.search_edit.addAction(QIcon.fromTheme("edit-find"), QLineEdit.ActionPosition.LeadingPosition)
        self.search_edit.textChanged.connect(self._filter_apps)
        search_sort_layout.addWidget(self.search_edit)

        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["Sort by Name", "Sort by Version"])
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

        self.empty_install_btn = QPushButton(QIcon.fromTheme("list-add"), "Install Your First AppImage")
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

        self.statusBar = QStatusBar(self)
        self.setStatusBar(self.statusBar)
        self.statusBar.showMessage("Ready")

    def _add_app_to_list(self, key: str, app_dir: str, version: str, display_name: str | None = None, icon_path: str | None = None):
        if display_name is None:
            display_name = key
        version_str = version if version and version != "unknown" else ""
        text = f"{display_name}" + (f"  (v{version_str})" if version_str else "")
        list_item = QListWidgetItem(text)
        list_item.setData(Qt.ItemDataRole.UserRole, key)
        list_item.setToolTip(
            f"Path: {app_dir}\n"
            f"Version: {version_str or 'unknown'}\n"
            f"Name: {key}"
        )
        if icon_path:
            pixmap = get_pixmap_from_file(icon_path, 32)
            if pixmap and not pixmap.isNull():
                list_item.setIcon(QIcon(pixmap))
        self.installed_list.addItem(list_item)
        self.installed_apps[key] = {
            "path": app_dir,
            "version": version,
            "desktop_file": find_desktop_for_app(key),
            "desktop_shortcut": find_desktop_shortcut(key),
            "display_name": display_name,
            "icon_path": icon_path,
        }

    def scan_installed(self):
        self.installed_apps.clear()
        self.installed_list.clear()
        install_dir = get_settings().get("install_dir", DEFAULT_INSTALL_DIR)
        seen = set()

        if os.path.isdir(install_dir):
            for item in sorted(os.listdir(install_dir)):
                app_dir = os.path.join(install_dir, item)
                if not os.path.isdir(app_dir):
                    continue
                seen.add(item)
                apprun = os.path.join(app_dir, "AppRun")
                if not os.path.isfile(apprun) or not os.access(apprun, os.X_OK):
                    continue
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
                self._add_app_to_list(item, app_dir, version, display_name, icon_path)

        registry = InstallationRegistry()
        for record in registry.get_all():
            if record.name not in seen:
                app_dir = record.path
                if app_dir and os.path.isdir(app_dir):
                    version = record.version or get_version(app_dir) or "?"
                    icon_path = self._find_app_icon(app_dir)
                    display_name = record.name
                    self._add_app_to_list(record.name, app_dir, version, display_name, icon_path)

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
        self.statusBar.showMessage(f"Found {count} installed app{'s' if count != 1 else ''}" if has_apps else "Ready — no AppImages installed yet")

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
        else:
            items.sort(key=lambda x: self.installed_apps.get(x[0], {}).get("version", "?"))
        for _, item in items:
            self.installed_list.addItem(item)

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
        progress = QProgressDialog("Reading AppImage metadata...", None, 0, 0, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setCancelButton(None)
        progress.show()

        result = {}

        worker = MetadataWorker(path)
        loop = QEventLoop()

        def on_finished(info, icon_data):
            result["info"] = info
            result["icon_data"] = icon_data
            loop.quit()

        worker.finished.connect(on_finished)
        worker.start()
        loop.exec()

        progress.close()
        info = result.get("info", {"Name": Path(path).stem})
        icon_data = result.get("icon_data")

        app_name = info.get("Name", Path(path).stem)

        # Security scan
        scan_result = scan_appimage(path)
        if scan_result["risk_level"] == "high":
            reply = QMessageBox.critical(
                self, "Security Warning",
                f"<b>High-risk file detected!</b><br><br>"
                f"<b>{Path(path).name}</b> was flagged by the security scanner:<br>"
                f"{'<br>'.join(scan_result.get('warnings', []))}<br><br>"
                f"SHA256: {scan_result['sha256'][:16]}...<br>"
                f"Size: {scan_result['size_mb']:.1f} MB<br><br>"
                "Installation is blocked for your safety.",
                QMessageBox.StandardButton.Ok,
            )
            return
        elif scan_result["risk_level"] == "medium":
            warning_text = "<br>".join(scan_result.get("warnings", [])) or "Suspicious patterns detected."
            reply = QMessageBox.warning(
                self, "Security Warning",
                f"<b>Medium-risk file</b><br><br>"
                f"<b>{Path(path).name}</b> raised security concerns:<br>"
                f"{warning_text}<br><br>"
                f"SHA256: {scan_result['sha256'][:16]}...<br>"
                f"Size: {scan_result['size_mb']:.1f} MB<br><br>"
                "Do you want to proceed anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        if self._is_already_installed(app_name, path):
            dlg = QDialog(self)
            dlg.setWindowTitle("Already Installed")
            dlg.setFixedSize(400, 200)
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

            btn_reinstall = QPushButton(QIcon.fromTheme("view-refresh"), "Re-integrate")
            btn_reinstall.clicked.connect(lambda: dlg.done(1))
            btn_row.addWidget(btn_reinstall)

            btn_remove = QPushButton(QIcon.fromTheme("edit-delete"), "Remove")
            btn_remove.clicked.connect(lambda: dlg.done(2))
            btn_row.addWidget(btn_remove)

            btn_cancel = QPushButton(QIcon.fromTheme("dialog-cancel"), "Cancel")
            btn_cancel.clicked.connect(lambda: dlg.done(0))
            btn_row.addWidget(btn_cancel)

            layout.addLayout(btn_row)
            reply = dlg.exec()

            if reply == 0:
                return
            elif reply == 2:
                self._uninstall_app(app_name)
                return

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
        run_action = menu.addAction(QIcon.fromTheme("media-playback-start"), "Run")
        update_action = menu.addAction(QIcon.fromTheme("emblem-downloads"), "Update...")
        uninstall_action = menu.addAction(QIcon.fromTheme("edit-delete"), "Uninstall")
        open_folder_action = menu.addAction(QIcon.fromTheme("folder-open"), "Open Folder")
        menu.addSeparator()
        has_shortcut = bool(app_info.get("desktop_shortcut"))
        shortcut_text = "Remove Desktop Shortcut" if has_shortcut else "Create Desktop Shortcut"
        shortcut_action = menu.addAction(QIcon.fromTheme("user-desktop"), shortcut_text)

        action = menu.exec(self.installed_list.mapToGlobal(pos))
        if action == run_action:
            self._run_app(app_name)
        elif action == update_action:
            self._update_app(app_name)
        elif action == uninstall_action:
            self._uninstall_app(app_name)
        elif action == open_folder_action:
            self._open_folder(app_name)
        elif action == shortcut_action:
            if has_shortcut:
                self._remove_desktop_shortcut(app_name)
            else:
                self._create_desktop_shortcut(app_name)

    def _run_app(self, app_name: str):
        apprun = os.path.join(self.installed_apps[app_name]["path"], "AppRun")
        if not os.path.exists(apprun):
            QMessageBox.critical(self, "Error", "AppRun not found.")
            return
        try:
            subprocess.Popen(
                [apprun],
                cwd=get_settings()["install_dir"],
                start_new_session=True,
            )
            self.statusBar.showMessage(f"Running {app_name}")
        except OSError as e:
            QMessageBox.critical(self, "Run Error", str(e))

    def _uninstall_app(self, app_name: str):
        reply = QMessageBox.question(
            self,
            "Uninstall",
            f"Remove '{app_name}' and all its files?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        app_info = self.installed_apps[app_name]
        app_dir = app_info["path"]

        desktop_file = find_desktop_for_app(app_name)
        if desktop_file and os.path.exists(desktop_file):
            os.remove(desktop_file)

        shortcut = find_desktop_shortcut(app_name)
        if shortcut and os.path.exists(shortcut):
            os.remove(shortcut)

        if os.path.exists(app_dir):
            shutil.rmtree(app_dir)

        for d in (app_dir + ".home", app_dir + ".config"):
            if os.path.exists(d):
                shutil.rmtree(d)

        registry = InstallationRegistry()
        registry.remove(app_name)

        try:
            refresh_desktop_database()
        except Exception:
            self.statusBar.showMessage("Warning: could not refresh desktop database")

        self.scan_installed()
        self.statusBar.showMessage(f"Uninstalled {app_name}")

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
                self.statusBar.showMessage("Warning: could not create backup before update")

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
                self.statusBar.showMessage(f"Warning: could not create desktop entry: {e}")

        registry = InstallationRegistry()
        record = registry.get(app_name)
        if record:
            record.version = version
            registry.add(record)

        self.scan_installed()
        self.statusBar.showMessage(f"Updated {app_name}")

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
            self.statusBar.showMessage(f"Desktop shortcut created for {app_name}")
            app_info["desktop_shortcut"] = shortcut_path
        else:
            QMessageBox.warning(self, "Error", "Failed to create desktop shortcut.")

    def _remove_desktop_shortcut(self, app_name: str):
        shortcut = find_desktop_shortcut(app_name)
        if shortcut and os.path.exists(shortcut):
            os.remove(shortcut)
            self.installed_apps[app_name]["desktop_shortcut"] = None
            self.statusBar.showMessage(f"Desktop shortcut removed for {app_name}")

    def _open_folder(self, app_name: str):
        app_dir = self.installed_apps[app_name]["path"]
        try:
            subprocess.Popen(["xdg-open", app_dir], start_new_session=True)
        except OSError:
            QMessageBox.critical(self, "Error", "Could not open folder.")

    def _open_build_dialog(self):
        dialog = BuildDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.statusBar.showMessage("AppImage built successfully!")
        else:
            self.statusBar.showMessage("Build cancelled or failed")

    def _check_all_updates(self):
        check_for_updates(self)

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
            "<b>License:</b> GNU General Public License v2<br>"
            "Copyright © 2026 putinservai-cyber<br><br>"
            "This program is free software; you can redistribute it<br>"
            "and/or modify it under the terms of the GNU General<br>"
            "Public License as published by the Free Software<br>"
            "Foundation; version 2 of the License."
        )
