import datetime
import hashlib
import json
import os
import shutil
import tempfile
import urllib.request
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QFrame, QSizePolicy,
    QPushButton, QTreeWidget, QTreeWidgetItem,
    QLineEdit, QProgressDialog, QMessageBox, QFileDialog,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox,
    QGroupBox, QGridLayout,
)

from niruvi.utils import get_icon
from niruvi.toggle_switch import ToggleSwitch
from niruvi.installation_registry import InstallationRegistry
from niruvi.update_sources import (
    resolve_update_source, normalize_update_url,
    detect_source_type, parse_github_repo, parse_gitlab_project,
)
from niruvi.self_update import compare_versions


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / 1024 ** 2:.1f} MB"
    else:
        return f"{size_bytes / 1024 ** 3:.2f} GB"


def _format_date(timestamp: float) -> str:
    dt = datetime.datetime.fromtimestamp(timestamp)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


class UpdateCheckWorker(QThread):
    finished = pyqtSignal(bool, str, str, str)
    error = pyqtSignal(str)

    def __init__(self, update_url: str, current_version: str, parent=None):
        super().__init__(parent)
        self.update_url = update_url
        self.current_version = current_version

    def run(self):
        try:
            info = resolve_update_source(self.update_url, self.current_version)
            if not info or not info.version:
                self.error.emit("Could not resolve update source")
                return
            available = compare_versions(info.version, 'gt', self.current_version)
            self.finished.emit(available, info.version, info.download_url, info.changelog or "")
        except Exception as e:
            self.error.emit(str(e))


class FileTreeWidget(QTreeWidget):
    def __init__(self, app_dir: str, parent=None):
        super().__init__(parent)
        self.setHeaderLabels(["Name", "Size", "Modified"])
        self.setColumnWidth(0, 260)
        self.setColumnWidth(1, 80)
        self.setAlternatingRowColors(True)
        self.setRootIsDecorated(True)
        self.setAnimated(True)
        self._populate(app_dir)

    def _populate(self, app_dir: str):
        def add_dir(parent_item, path: str):
            try:
                entries = sorted(os.listdir(path))
            except OSError:
                return
            for name in entries:
                full = os.path.join(path, name)
                try:
                    is_dir = os.path.isdir(full)
                    size = ""
                    mtime = ""
                    if not is_dir:
                        size = _format_size(os.path.getsize(full))
                    mtime = _format_date(os.path.getmtime(full))
                    item = QTreeWidgetItem(parent_item if parent_item else [self.invisibleRootItem()])
                    item.setText(0, name + "/" if is_dir else name)
                    item.setText(1, size)
                    item.setText(2, mtime)
                    item.setData(0, Qt.ItemDataRole.UserRole, full)
                    if is_dir:
                        add_dir(item, full)
                except OSError:
                    pass

        root = self.invisibleRootItem()
        try:
            for name in sorted(os.listdir(app_dir)):
                full = os.path.join(app_dir, name)
                is_dir = os.path.isdir(full)
                size = ""
                mtime = ""
                if not is_dir:
                    size = _format_size(os.path.getsize(full))
                mtime = _format_date(os.path.getmtime(full))
                item = QTreeWidgetItem(root)
                item.setText(0, name + "/" if is_dir else name)
                item.setText(1, size)
                item.setText(2, mtime)
                item.setData(0, Qt.ItemDataRole.UserRole, full)
                if is_dir:
                    add_dir(item, full)
        except OSError:
            pass


class AppInfoDialog(QDialog):
    def __init__(self, app_name: str, app_info: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{app_name} — App Info")
        self.setMinimumSize(680, 620)
        self.resize(780, 680)
        self.setModal(True)
        self._app_name = app_name
        self._info = app_info
        self._update_worker = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(12)

        icon_path = self._info.get("icon_path")
        icon_label = QLabel()
        if icon_path and os.path.exists(icon_path):
            pixmap = QPixmap(icon_path)
            if not pixmap.isNull():
                icon_label.setPixmap(pixmap.scaled(
                    48, 48, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
        else:
            icon_label.setPixmap(
                get_icon("package-x-generic", "application-x-archive").pixmap(48, 48)
            )
        icon_label.setFixedSize(48, 48)
        header.addWidget(icon_label)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)

        display_name = self._info.get("display_name", self._app_name)
        title = QLabel(f"<b>{display_name}</b>")
        font = title.font()
        font.setPointSize(16)
        title.setFont(font)
        title_col.addWidget(title)

        version_str = self._info.get("version", "unknown")
        ver = QLabel(f"Version: {version_str}")
        ver.setStyleSheet("color: palette(window-text);")
        title_col.addWidget(ver)

        header.addLayout(title_col, 1)
        layout.addLayout(header)

        layout.addWidget(QFrame(frameShape=QFrame.Shape.HLine, frameShadow=QFrame.Shadow.Sunken))

        registry = InstallationRegistry()
        record = registry.get(self._app_name)

        fields = []
        app_dir = self._info.get("path", "?")
        fields.append(("App Name", self._app_name))

        if os.path.isdir(app_dir):
            total = 0
            for root, dirs, files in os.walk(app_dir):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
            fields.append(("Size", _format_size(total)))
            ctime = os.path.getctime(app_dir)
            fields.append(("Installed", _format_date(ctime)))

        if record and record.architecture:
            fields.append(("Architecture", record.architecture))
        else:
            arch = self._info.get("architecture", "")
            if arch:
                fields.append(("Architecture", arch))

        if record and record.source_sha256:
            sha = record.source_sha256
            fields.append(("SHA256", f"{sha[:16]}...{sha[-16:]}"))

        app_type = self._info.get("Type", "")
        if app_type:
            fields.append(("Type", app_type))

        fields.append(("Path", app_dir))

        desktop_file = self._info.get("desktop_file")
        if desktop_file:
            fields.append(("Desktop Entry", desktop_file))

        shortcut = self._info.get("desktop_shortcut")
        if shortcut:
            fields.append(("Shortcut", shortcut))

        for label, value in fields:
            row = QHBoxLayout()
            row.setSpacing(8)
            lbl = QLabel(f"<b>{label}:</b>")
            lbl.setFixedWidth(130)
            val = QLabel(str(value))
            val.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            val.setWordWrap(True)
            val.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            row.addWidget(lbl)
            row.addWidget(val, 1)
            layout.addLayout(row)

        layout.addWidget(QFrame(frameShape=QFrame.Shape.HLine, frameShadow=QFrame.Shadow.Sunken))

        customization_group = QGroupBox("Customization")
        cust_layout = QVBoxLayout(customization_group)
        cust_layout.setSpacing(8)

        display_name_layout = QHBoxLayout()
        display_name_layout.addWidget(QLabel("Display name:"))
        self.display_name_edit = QLineEdit()
        self.display_name_edit.setPlaceholderText("Override display name (leave empty for default)")
        if record and record.display_name_override:
            self.display_name_edit.setText(record.display_name_override)
        display_name_layout.addWidget(self.display_name_edit, 1)
        self.btn_save_display_name = QPushButton(get_icon("document-save"), "Save")
        self.btn_save_display_name.clicked.connect(self._save_display_name)
        display_name_layout.addWidget(self.btn_save_display_name)
        cust_layout.addLayout(display_name_layout)

        icon_row = QHBoxLayout()
        self.custom_icon_label = QLabel()
        self.custom_icon_label.setFixedSize(24, 24)
        icon_row.addWidget(self.custom_icon_label)
        self.btn_pick_icon = QPushButton(get_icon("image-x-generic"), "Choose Custom Icon...")
        self.btn_pick_icon.clicked.connect(self._pick_custom_icon)
        icon_row.addWidget(self.btn_pick_icon)
        self.btn_clear_icon = QPushButton(get_icon("edit-delete"), "Clear")
        self.btn_clear_icon.clicked.connect(self._clear_custom_icon)
        icon_row.addWidget(self.btn_clear_icon)
        icon_row.addStretch()
        cust_layout.addLayout(icon_row)
        self._update_custom_icon_preview()

        run_args_layout = QHBoxLayout()
        run_args_layout.addWidget(QLabel("Run arguments:"))
        self.run_args_edit = QLineEdit()
        self.run_args_edit.setPlaceholderText("e.g. --verbose --config=myconfig.conf")
        if record and record.run_args:
            self.run_args_edit.setText(record.run_args)
        run_args_layout.addWidget(self.run_args_edit, 1)
        self.btn_save_run_args = QPushButton(get_icon("document-save"), "Save")
        self.btn_save_run_args.clicked.connect(self._save_run_args)
        run_args_layout.addWidget(self.btn_save_run_args)
        cust_layout.addLayout(run_args_layout)

        env_label = QLabel("Environment variables:")
        cust_layout.addWidget(env_label)
        self.env_table = QTableWidget()
        self.env_table.setColumnCount(2)
        self.env_table.setHorizontalHeaderLabels(["Variable", "Value"])
        self.env_table.horizontalHeader().setStretchLastSection(True)
        self.env_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.env_table.setMinimumHeight(100)
        if record and record.env_vars:
            self._populate_env_table(record.env_vars)
        cust_layout.addWidget(self.env_table)

        env_btn_row = QHBoxLayout()
        self.btn_add_env = QPushButton(get_icon("list-add"), "Add")
        self.btn_add_env.clicked.connect(self._add_env_row)
        env_btn_row.addWidget(self.btn_add_env)
        self.btn_remove_env = QPushButton(get_icon("list-remove"), "Remove Selected")
        self.btn_remove_env.clicked.connect(self._remove_env_row)
        env_btn_row.addWidget(self.btn_remove_env)
        self.btn_save_env = QPushButton(get_icon("document-save"), "Save")
        self.btn_save_env.clicked.connect(self._save_env_vars)
        env_btn_row.addWidget(self.btn_save_env)
        env_btn_row.addStretch()
        cust_layout.addLayout(env_btn_row)

        layout.addWidget(customization_group)

        layout.addWidget(QFrame(frameShape=QFrame.Shape.HLine, frameShadow=QFrame.Shadow.Sunken))

        update_group = QGroupBox("Updates")
        update_layout = QVBoxLayout(update_group)
        update_layout.setSpacing(8)

        url_layout = QHBoxLayout()
        url_layout.addWidget(QLabel("Update URL:"))
        self.update_url_edit = QLineEdit()
        self.update_url_edit.setPlaceholderText(
            "GitHub/GitLab repo URL or direct link to update manifest"
        )
        if record and record.update_url:
            self.update_url_edit.setText(record.update_url)
        url_layout.addWidget(self.update_url_edit, 1)
        update_layout.addLayout(url_layout)

        url_btn_row = QHBoxLayout()
        self.btn_detect_source = QPushButton(get_icon("network-server"), "Auto-detect")
        self.btn_detect_source.setToolTip("Detect update source type (GitHub/GitLab/direct)")
        self.btn_detect_source.clicked.connect(self._auto_detect_source)
        url_btn_row.addWidget(self.btn_detect_source)

        self.btn_check_update = QPushButton(get_icon("emblem-downloads"), "Check for Updates")
        self.btn_check_update.clicked.connect(self._check_for_updates)
        url_btn_row.addWidget(self.btn_check_update)

        self.btn_save_url = QPushButton(get_icon("document-save"), "Save URL")
        self.btn_save_url.clicked.connect(self._save_update_url)
        url_btn_row.addWidget(self.btn_save_url)

        url_btn_row.addStretch()
        update_layout.addLayout(url_btn_row)

        self.source_type_label = QLabel("")
        self.source_type_label.setStyleSheet("color: palette(disabled);")
        update_layout.addWidget(self.source_type_label)
        self._update_source_type_label()

        channel_row = QHBoxLayout()
        channel_row.addWidget(QLabel("Update channel:"))
        self.channel_combo = QComboBox()
        self.channel_combo.addItems(["stable", "beta", "nightly"])
        if record and record.update_channel:
            idx = self.channel_combo.findText(record.update_channel)
            if idx >= 0:
                self.channel_combo.setCurrentIndex(idx)
        self.channel_combo.currentTextChanged.connect(self._save_channel)
        channel_row.addWidget(self.channel_combo)
        channel_row.addStretch()
        update_layout.addLayout(channel_row)

        auto_update_row = QHBoxLayout()
        auto_update_row.addWidget(QLabel("Auto-update in background:"))
        self.auto_update_toggle = ToggleSwitch(self)
        if record and record.auto_update:
            self.auto_update_toggle.setChecked(True)
        self.auto_update_toggle.toggled.connect(self._save_auto_update)
        auto_update_row.addWidget(self.auto_update_toggle)
        auto_update_row.addStretch()
        update_layout.addLayout(auto_update_row)

        layout.addWidget(update_group)

        layout.addWidget(QFrame(frameShape=QFrame.Shape.HLine, frameShadow=QFrame.Shadow.Sunken))

        file_tree_label = QLabel("<b>Files:</b>")
        layout.addWidget(file_tree_label)

        if os.path.isdir(app_dir):
            tree = FileTreeWidget(app_dir)
            layout.addWidget(tree, 1)

        layout.addStretch()

        btn_layout = QHBoxLayout()

        self.btn_run = QPushButton(get_icon("media-playback-start"), "Run")
        self.btn_run.clicked.connect(lambda: self._run_app())
        btn_layout.addWidget(self.btn_run)

        self.btn_uninstall = QPushButton(get_icon("edit-delete"), "Uninstall")
        self.btn_uninstall.clicked.connect(lambda: self._uninstall_app())
        btn_layout.addWidget(self.btn_uninstall)

        btn_layout.addStretch()

        close_btn = QPushButton(get_icon("dialog-close"), "Close")
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

    def _save_display_name(self):
        override = self.display_name_edit.text().strip()
        record = self._get_or_create_record()
        record.display_name_override = override
        registry = InstallationRegistry()
        registry.add(record)
        self._info["display_name"] = override or self._app_name
        parent = self.parent()
        if parent and hasattr(parent, "_status_bar"):
            parent._status_bar.showMessage(f"Display name saved for {self._app_name}")

    def _pick_custom_icon(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Custom Icon", "",
            "Images (*.png *.svg *.xpm *.ico);;All files (*)",
        )
        if not path:
            return
        dest = os.path.join(self._info.get("path", ""), ".custom-icon.png")
        try:
            shutil.copy2(path, dest)
            record = self._get_or_create_record()
            record.custom_icon_path = dest
            registry = InstallationRegistry()
            registry.add(record)
            self._info["custom_icon_path"] = dest
            self._update_custom_icon_preview()
            parent = self.parent()
            if parent and hasattr(parent, "_status_bar"):
                parent._status_bar.showMessage(f"Custom icon set for {self._app_name}")
        except OSError as e:
            QMessageBox.critical(self, "Error", f"Could not set custom icon:\n{e}")

    def _clear_custom_icon(self):
        record = self._get_or_create_record()
        record.custom_icon_path = ""
        registry = InstallationRegistry()
        registry.add(record)
        self._info["custom_icon_path"] = ""
        self._update_custom_icon_preview()
        parent = self.parent()
        if parent and hasattr(parent, "_status_bar"):
            parent._status_bar.showMessage(f"Custom icon cleared for {self._app_name}")

    def _update_custom_icon_preview(self):
        icon_path = self._info.get("custom_icon_path") or ""
        if icon_path and os.path.isfile(icon_path):
            pixmap = QPixmap(icon_path)
            if not pixmap.isNull():
                self.custom_icon_label.setPixmap(pixmap.scaled(
                    24, 24, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
                return
        self.custom_icon_label.clear()

    def _get_or_create_record(self):
        registry = InstallationRegistry()
        record = registry.get(self._app_name)
        if not record:
            from niruvi.installation_registry import InstallationRecord
            record = InstallationRecord(
                name=self._app_name,
                path=self._info.get("path", ""),
                version=self._info.get("version", ""),
            )
        return record

    def _save_run_args(self):
        args = self.run_args_edit.text().strip()
        record = self._get_or_create_record()
        record.run_args = args
        registry = InstallationRegistry()
        registry.add(record)
        parent = self.parent()
        if parent and hasattr(parent, "_status_bar"):
            parent._status_bar.showMessage(f"Run arguments saved for {self._app_name}")

    def _populate_env_table(self, env_vars: dict):
        self.env_table.setRowCount(len(env_vars))
        for i, (key, val) in enumerate(sorted(env_vars.items())):
            self.env_table.setItem(i, 0, QTableWidgetItem(key))
            self.env_table.setItem(i, 1, QTableWidgetItem(val))

    def _add_env_row(self):
        row = self.env_table.rowCount()
        self.env_table.insertRow(row)
        self.env_table.setItem(row, 0, QTableWidgetItem(""))
        self.env_table.setItem(row, 1, QTableWidgetItem(""))
        self.env_table.editItem(self.env_table.item(row, 0))

    def _remove_env_row(self):
        rows = set()
        for item in self.env_table.selectedItems():
            rows.add(item.row())
        for row in sorted(rows, reverse=True):
            self.env_table.removeRow(row)

    def _save_env_vars(self):
        env_vars = {}
        for i in range(self.env_table.rowCount()):
            key_item = self.env_table.item(i, 0)
            val_item = self.env_table.item(i, 1)
            key = key_item.text().strip() if key_item else ""
            val = val_item.text().strip() if val_item else ""
            if key:
                env_vars[key] = val
        record = self._get_or_create_record()
        record.env_vars = env_vars
        registry = InstallationRegistry()
        registry.add(record)
        parent = self.parent()
        if parent and hasattr(parent, "_status_bar"):
            parent._status_bar.showMessage(f"Environment variables saved for {self._app_name}")

    def _save_update_url(self):
        url = self.update_url_edit.text().strip()
        normalized = normalize_update_url(url) if url else ""
        record = self._get_or_create_record()
        record.update_url = normalized
        registry = InstallationRegistry()
        registry.add(record)
        self._info["update_url"] = normalized
        self._update_source_type_label()
        parent = self.parent()
        if parent and hasattr(parent, "_status_bar"):
            parent._status_bar.showMessage(f"Update URL saved for {self._app_name}")

    def _update_source_type_label(self):
        url = self.update_url_edit.text().strip()
        if url:
            from niruvi.update_sources import detect_source_type
            st = detect_source_type(url)
            labels = {"github": "GitHub Releases", "gitlab": "GitLab Releases", "direct": "Direct URL"}
            self.source_type_label.setText(f"Source: {labels.get(st, st)}")
        else:
            self.source_type_label.setText("")

    def _auto_detect_source(self):
        url = self.update_url_edit.text().strip()
        if not url:
            QMessageBox.information(
                self, "No URL",
                "Enter a GitHub or GitLab repository URL first."
            )
            return
        from niruvi.update_sources import detect_source_type, resolve_update_source
        st = detect_source_type(url)
        if st == "github":
            repo = parse_github_repo(url)
            if repo:
                QMessageBox.information(
                    self, "GitHub Repository Detected",
                    f"Owner: {repo[0]}\nRepo: {repo[1]}\n\n"
                    "Niruvi will auto-detect the latest release from GitHub when checking for updates."
                )
            self._update_source_type_label()
        elif st == "gitlab":
            project = parse_gitlab_project(url)
            if project:
                QMessageBox.information(
                    self, "GitLab Project Detected",
                    f"Project: {project}\n\n"
                    "Niruvi will auto-detect the latest release from GitLab when checking for updates."
                )
            self._update_source_type_label()
        else:
            QMessageBox.information(
                self, "Direct URL",
                "This URL will be used as a direct download link for updates."
            )
        self._save_update_url()

    def _save_channel(self, channel: str):
        record = self._get_or_create_record()
        record.update_channel = channel
        registry = InstallationRegistry()
        registry.add(record)

    def _save_auto_update(self, enabled: bool):
        record = self._get_or_create_record()
        record.auto_update = enabled
        registry = InstallationRegistry()
        registry.add(record)

    def _check_for_updates(self):
        url = self.update_url_edit.text().strip()
        if not url:
            QMessageBox.information(
                self, "No Update URL",
                "No update URL configured. Enter a GitHub/GitLab repo URL or direct update manifest URL."
            )
            return

        current_version = self._info.get("version", "")
        if not current_version or current_version == "unknown":
            QMessageBox.information(
                self, "Unknown Version",
                "Current version is unknown. Update check requires a known version string."
            )
            return

        self.btn_check_update.setEnabled(False)
        self.btn_check_update.setText("Checking...")

        normalized = normalize_update_url(url)
        self._update_worker = UpdateCheckWorker(normalized, current_version)
        self._update_worker.finished.connect(self._on_update_check_done)
        self._update_worker.error.connect(self._on_update_check_error)
        self._update_worker.start()

    def _on_update_check_done(self, available: bool, latest: str, download_url: str, changelog: str):
        self.btn_check_update.setEnabled(True)
        self.btn_check_update.setText("Check for Updates")

        if available:
            msg = (
                f"Version {latest} is available for {self._app_name}.\n\n"
                f"Current: {self._info.get('version', 'unknown')}\n"
                f"New: {latest}\n"
            )
            if changelog:
                msg += f"\nWhat's new:\n{changelog[:500]}"
            reply = QMessageBox.question(
                self, "Update Available",
                msg + "\n\nDo you want to download and install it now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._download_and_update(download_url, latest)
        else:
            QMessageBox.information(
                self, "Up to Date",
                f"{self._app_name} (version {self._info.get('version', '?')}) is already the latest version."
            )

    def _on_update_check_error(self, error_msg: str):
        self.btn_check_update.setEnabled(True)
        self.btn_check_update.setText("Check for Updates")
        QMessageBox.warning(
            self, "Update Check Failed",
            f"Could not check for updates:\n{error_msg}"
        )

    def _download_and_update(self, download_url: str, latest_version: str):
        progress = QProgressDialog(f"Downloading {self._app_name} update...", "Cancel", 0, 100, self)
        progress.setWindowTitle("Downloading Update")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setAutoClose(True)
        progress.setValue(0)

        temp_path = None
        dest_dir = self._info.get("path", "")

        try:
            fd, temp_path = tempfile.mkstemp(suffix=".AppImage")
            os.close(fd)

            resp = urllib.request.urlopen(download_url, timeout=120)
            total = int(resp.headers.get("Content-Length", 0))
            sha256_hash = hashlib.sha256()
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
                    sha256_hash.update(chunk)
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
            record = registry.get(self._app_name)
            if record:
                record.version = version
                registry.add(record)

            QMessageBox.information(
                self, "Update Complete",
                f"{self._app_name} has been updated to version {version}."
            )

            parent = self.parent()
            if parent and hasattr(parent, "_status_bar"):
                parent._status_bar.showMessage(f"Updated {self._app_name} to version {version}")

        except Exception as e:
            progress.close()
            QMessageBox.critical(self, "Update Failed", f"Failed to update:\n{e}")

    def _run_app(self):
        parent = self.parent()
        if parent and hasattr(parent, "_run_app"):
            parent._run_app(self._app_name)

    def _uninstall_app(self):
        self.accept()
        parent = self.parent()
        if parent and hasattr(parent, "_uninstall_app"):
            parent._uninstall_app(self._app_name)
