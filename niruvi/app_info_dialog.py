import datetime
import hashlib
import json
import os
import subprocess
import shutil
import tempfile
import urllib.request
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QPixmap, QFont
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QFrame, QSizePolicy, QScrollArea,
    QPushButton, QTreeWidget, QTreeWidgetItem,
    QLineEdit, QProgressDialog, QMessageBox, QFileDialog,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox,
    QGroupBox, QWidget,
)

from niruvi.utils import get_icon
from niruvi.toggle_switch import ToggleSwitch
from niruvi.installation_registry import InstallationRegistry
from niruvi.update_sources import (
    resolve_update_source, normalize_update_url,
    detect_source_type, parse_github_repo, parse_gitlab_project,
)
from niruvi.self_update import compare_versions

_SECTION_STYLE = """
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

_CARD_STYLE = """
#card {
    background: palette(window);
    border: 1px solid palette(midlight);
    border-radius: 8px;
    padding: 16px;
}
"""


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
    return datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def _get_gpu_info() -> list[str]:
    info = []
    try:
        result = subprocess.run(
            ["glxinfo", "-B"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                low = line.lower()
                if "opengl renderer" in low or "opengl vendor" in low or "opengl version" in low:
                    info.append(line.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        info.append("glxinfo not available")
    try:
        result = subprocess.run(
            ["vulkaninfo", "--summary"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                low = line.strip().lower()
                if low.startswith("gpu") or "device name" in low or "driver version" in low or "vulkan" in low:
                    val = line.strip()
                    if val and val not in info:
                        info.append(val)
        else:
            info.append("vulkaninfo: no Vulkan drivers found")
    except FileNotFoundError:
        info.append("vulkaninfo not available")
    except subprocess.TimeoutExpired:
        info.append("vulkaninfo timed out")
    if not info:
        info.append("No GPU information available")
    return info


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
                    item = QTreeWidgetItem(parent_item if parent_item else self.invisibleRootItem())
                    item.setText(0, name + "/" if is_dir else name)
                    item.setText(1, _format_size(os.path.getsize(full)) if not is_dir else "")
                    item.setText(2, _format_date(os.path.getmtime(full)))
                    item.setData(0, Qt.ItemDataRole.UserRole, full)
                    if is_dir:
                        add_dir(item, full)
                except OSError:
                    pass
        try:
            for name in sorted(os.listdir(app_dir)):
                full = os.path.join(app_dir, name)
                is_dir = os.path.isdir(full)
                item = QTreeWidgetItem(self.invisibleRootItem())
                item.setText(0, name + "/" if is_dir else name)
                item.setText(1, _format_size(os.path.getsize(full)) if not is_dir else "")
                item.setText(2, _format_date(os.path.getmtime(full)))
                item.setData(0, Qt.ItemDataRole.UserRole, full)
                if is_dir:
                    add_dir(item, full)
        except OSError:
            pass


class AppInfoDialog(QDialog):
    def __init__(self, app_name: str, app_info: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{app_name} — App Info")
        self.setMinimumSize(640, 580)
        self.resize(760, 680)
        self.setModal(True)
        self._app_name = app_name
        self._info = app_info
        self._update_worker = None
        self._init_ui()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 20, 20, 0)
        layout.setSpacing(12)

        registry = InstallationRegistry()
        record = registry.get(self._app_name)
        app_dir = self._info.get("path", "?")

        # ── Header Card ──
        header_card = QWidget()
        header_card.setObjectName("card")
        header_card.setStyleSheet(_CARD_STYLE)
        header_layout = QHBoxLayout(header_card)
        header_layout.setContentsMargins(16, 16, 16, 16)
        header_layout.setSpacing(16)

        icon_label = QLabel()
        icon_path = self._info.get("icon_path")
        if icon_path and os.path.exists(icon_path):
            pixmap = QPixmap(icon_path)
            if not pixmap.isNull():
                icon_label.setPixmap(pixmap.scaled(56, 56, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            icon_label.setPixmap(get_icon("package-x-generic", "application-x-archive").pixmap(56, 56))
        icon_label.setFixedSize(56, 56)
        header_layout.addWidget(icon_label)

        info_col = QVBoxLayout()
        info_col.setSpacing(4)
        display_name = self._info.get("display_name", self._app_name)
        title = QLabel(display_name)
        tf = title.font()
        tf.setPointSize(18)
        tf.setBold(True)
        title.setFont(tf)
        info_col.addWidget(title)

        version_str = self._info.get("version", "unknown")
        sub = QLabel(f"Version {version_str}  ·  {self._app_name}")
        sub.setStyleSheet("color: palette(disabled-text); font-size: 12px;")
        info_col.addWidget(sub)

        header_layout.addLayout(info_col, 1)
        layout.addWidget(header_card)

        # ── Details Section ──
        details_group = QGroupBox("Details")
        details_group.setStyleSheet(_SECTION_STYLE)
        details_grid = QVBoxLayout(details_group)
        details_grid.setSpacing(6)

        def add_field(label, value):
            row = QHBoxLayout()
            row.setSpacing(8)
            lbl = QLabel(label)
            lbl.setFixedWidth(120)
            lbl.setStyleSheet("font-weight: bold; color: palette(disabled-text); font-size: 12px;")
            val = QLabel(str(value))
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            val.setWordWrap(True)
            val.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            row.addWidget(lbl)
            row.addWidget(val, 1)
            details_grid.addLayout(row)

        add_field("Name", self._app_name)
        add_field("Path", app_dir)
        if os.path.isdir(app_dir):
            total = 0
            for dirpath, _, files in os.walk(app_dir):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(dirpath, f))
                    except OSError:
                        pass
            add_field("Size", _format_size(total))
            add_field("Installed", _format_date(os.path.getctime(app_dir)))
        arch = (record and record.architecture) or self._info.get("architecture", "")
        if arch:
            add_field("Architecture", arch)
        if record and record.source_sha256:
            s = record.source_sha256
            add_field("SHA256", f"{s[:16]}...{s[-16:]}")
        app_type = self._info.get("Type", "")
        if app_type:
            add_field("Type", app_type)
        desktop_file = self._info.get("desktop_file")
        if desktop_file:
            add_field("Desktop Entry", desktop_file)
        shortcut = self._info.get("desktop_shortcut")
        if shortcut:
            add_field("Shortcut", shortcut)

        layout.addWidget(details_group)

        # ── Customization Section ──
        cust_group = QGroupBox("Customization")
        cust_group.setStyleSheet(_SECTION_STYLE)
        cust_grid = QVBoxLayout(cust_group)
        cust_grid.setSpacing(8)

        name_row = QHBoxLayout()
        name_label = QLabel("Display name")
        name_label.setFixedWidth(120)
        name_label.setStyleSheet("color: palette(disabled-text); font-size: 12px;")
        name_row.addWidget(name_label)
        self.display_name_edit = QLineEdit()
        self.display_name_edit.setPlaceholderText("Override name (leave empty for default)")
        if record and record.display_name_override:
            self.display_name_edit.setText(record.display_name_override)
        name_row.addWidget(self.display_name_edit, 1)
        self.btn_save_display_name = QPushButton(get_icon("document-save"), "Save")
        self.btn_save_display_name.setFixedWidth(70)
        self.btn_save_display_name.clicked.connect(self._save_display_name)
        name_row.addWidget(self.btn_save_display_name)
        cust_grid.addLayout(name_row)

        icon_row = QHBoxLayout()
        icon_label_2 = QLabel("Custom icon")
        icon_label_2.setFixedWidth(120)
        icon_label_2.setStyleSheet("color: palette(disabled-text); font-size: 12px;")
        icon_row.addWidget(icon_label_2)
        self.custom_icon_preview = QLabel()
        self.custom_icon_preview.setFixedSize(24, 24)
        icon_row.addWidget(self.custom_icon_preview)
        self.btn_pick_icon = QPushButton(get_icon("image-x-generic"), "Choose...")
        self.btn_pick_icon.clicked.connect(self._pick_custom_icon)
        icon_row.addWidget(self.btn_pick_icon)
        self.btn_clear_icon = QPushButton(get_icon("edit-delete"), "Clear")
        self.btn_clear_icon.clicked.connect(self._clear_custom_icon)
        icon_row.addWidget(self.btn_clear_icon)
        icon_row.addStretch()
        cust_grid.addLayout(icon_row)
        self._update_icon_preview()

        args_row = QHBoxLayout()
        args_label = QLabel("Run arguments")
        args_label.setFixedWidth(120)
        args_label.setStyleSheet("color: palette(disabled-text); font-size: 12px;")
        args_row.addWidget(args_label)
        self.run_args_edit = QLineEdit()
        self.run_args_edit.setPlaceholderText("e.g. --verbose --config=myconfig.conf")
        if record and record.run_args:
            self.run_args_edit.setText(record.run_args)
        args_row.addWidget(self.run_args_edit, 1)
        self.btn_save_run_args = QPushButton(get_icon("document-save"), "Save")
        self.btn_save_run_args.setFixedWidth(70)
        self.btn_save_run_args.clicked.connect(self._save_run_args)
        args_row.addWidget(self.btn_save_run_args)
        cust_grid.addLayout(args_row)

        env_label = QLabel("Environment variables")
        env_label.setStyleSheet("color: palette(disabled-text); font-size: 12px;")
        cust_grid.addWidget(env_label)

        self.env_table = QTableWidget()
        self.env_table.setColumnCount(2)
        self.env_table.setHorizontalHeaderLabels(["Variable", "Value"])
        self.env_table.horizontalHeader().setStretchLastSection(True)
        self.env_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.env_table.setMinimumHeight(80)
        self.env_table.setMaximumHeight(160)
        if record and record.env_vars:
            self._populate_env_table(record.env_vars)
        cust_grid.addWidget(self.env_table)

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
        cust_grid.addLayout(env_btn_row)

        layout.addWidget(cust_group)

        # ── Updates Section ──
        update_group = QGroupBox("Updates")
        update_group.setStyleSheet(_SECTION_STYLE)
        update_grid = QVBoxLayout(update_group)
        update_grid.setSpacing(8)

        url_label_row = QHBoxLayout()
        url_lbl = QLabel("Update URL")
        url_lbl.setFixedWidth(120)
        url_lbl.setStyleSheet("color: palette(disabled-text); font-size: 12px;")
        url_label_row.addWidget(url_lbl)
        self.source_type_label = QLabel("")
        self.source_type_label.setStyleSheet("color: palette(disabled-text); font-size: 11px;")
        url_label_row.addWidget(self.source_type_label, 1)
        update_grid.addLayout(url_label_row)

        url_row = QHBoxLayout()
        self.update_url_edit = QLineEdit()
        self.update_url_edit.setPlaceholderText("GitHub/GitLab repo URL or direct download link")
        if record and record.update_url:
            self.update_url_edit.setText(record.update_url)
        url_row.addWidget(self.update_url_edit, 1)
        self.btn_save_url = QPushButton(get_icon("document-save"), "Save")
        self.btn_save_url.setFixedWidth(70)
        self.btn_save_url.clicked.connect(self._save_update_url)
        url_row.addWidget(self.btn_save_url)
        update_grid.addLayout(url_row)
        self._update_source_type_label()

        url_btn_row = QHBoxLayout()
        self.btn_detect_source = QPushButton(get_icon("network-server"), "Auto-detect Source")
        self.btn_detect_source.setToolTip("Detect whether the URL is a GitHub repo, GitLab project, or direct link")
        self.btn_detect_source.clicked.connect(self._auto_detect_source)
        url_btn_row.addWidget(self.btn_detect_source)
        self.btn_check_update = QPushButton(get_icon("emblem-downloads"), "Check for Updates")
        self.btn_check_update.setToolTip("Check if a newer version is available")
        self.btn_check_update.clicked.connect(self._check_for_updates)
        url_btn_row.addWidget(self.btn_check_update)
        url_btn_row.addStretch()
        update_grid.addLayout(url_btn_row)

        settings_row = QHBoxLayout()
        settings_row.setSpacing(24)
        channel_box = QHBoxLayout()
        channel_box.setSpacing(6)
        channel_box.addWidget(QLabel("Channel:"))
        self.channel_combo = QComboBox()
        self.channel_combo.addItems(["stable", "beta", "nightly"])
        if record and record.update_channel:
            idx = self.channel_combo.findText(record.update_channel)
            if idx >= 0:
                self.channel_combo.setCurrentIndex(idx)
        self.channel_combo.currentTextChanged.connect(self._save_channel)
        channel_box.addWidget(self.channel_combo)
        settings_row.addLayout(channel_box)

        auto_box = QHBoxLayout()
        auto_box.setSpacing(6)
        auto_box.addWidget(QLabel("Auto-update in background:"))
        self.auto_update_toggle = ToggleSwitch(self)
        if record and record.auto_update:
            self.auto_update_toggle.setChecked(True)
        self.auto_update_toggle.toggled.connect(self._save_auto_update)
        auto_box.addWidget(self.auto_update_toggle)
        settings_row.addLayout(auto_box)
        settings_row.addStretch()
        update_grid.addLayout(settings_row)

        layout.addWidget(update_group)

        # ── GPU Diagnostics Section ──
        gpu_group = QGroupBox("GPU / Vulkan")
        gpu_group.setStyleSheet(_SECTION_STYLE)
        gpu_layout = QVBoxLayout(gpu_group)
        gpu_layout.setSpacing(4)
        gpu_info = _get_gpu_info()
        if gpu_info:
            for line in gpu_info:
                lbl = QLabel(line)
                lbl.setWordWrap(True)
                lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                gpu_layout.addWidget(lbl)
        else:
            gpu_layout.addWidget(QLabel("No GPU information available."))
        refresh_gpu_btn = QPushButton(get_icon("view-refresh"), "Refresh GPU Info")
        refresh_gpu_btn.clicked.connect(lambda: self._refresh_gpu_info(gpu_layout))
        gpu_layout.addWidget(refresh_gpu_btn)
        layout.addWidget(gpu_group)

        # ── Files Section ──
        files_group = QGroupBox("Files")
        files_group.setStyleSheet(_SECTION_STYLE)
        files_layout = QVBoxLayout(files_group)
        if os.path.isdir(app_dir):
            tree = FileTreeWidget(app_dir)
            tree.setMinimumHeight(120)
            files_layout.addWidget(tree, 1)
        else:
            files_layout.addWidget(QLabel("App directory not found."))
        layout.addWidget(files_group)

        layout.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        # ── Bottom Action Bar ──
        action_bar = QFrame()
        action_bar.setFrameShape(QFrame.Shape.NoFrame)
        action_bar.setStyleSheet("background: palette(window); border-top: 1px solid palette(midlight);")
        action_layout = QHBoxLayout(action_bar)
        action_layout.setContentsMargins(20, 10, 20, 10)
        action_layout.setSpacing(8)

        self.btn_run = QPushButton(get_icon("media-playback-start"), "Run")
        self.btn_run.setStyleSheet("QPushButton { padding: 8px 18px; font-weight: bold; }")
        self.btn_run.clicked.connect(lambda: self._run_app())
        action_layout.addWidget(self.btn_run)

        self.btn_uninstall = QPushButton(get_icon("edit-delete"), "Uninstall")
        self.btn_uninstall.setStyleSheet("QPushButton { padding: 8px 18px; color: #c44; }")
        self.btn_uninstall.clicked.connect(lambda: self._uninstall_app())
        action_layout.addWidget(self.btn_uninstall)

        action_layout.addStretch()

        close_btn = QPushButton(get_icon("dialog-close"), "Close")
        close_btn.setStyleSheet("QPushButton { padding: 8px 18px; }")
        close_btn.clicked.connect(self.accept)
        action_layout.addWidget(close_btn)

        root.addWidget(action_bar)

    def _save_display_name(self):
        override = self.display_name_edit.text().strip()
        record = self._get_or_create_record()
        record.display_name_override = override
        InstallationRegistry().add(record)
        self._info["display_name"] = override or self._app_name
        self._status(f"Display name saved for {self._app_name}")

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
            InstallationRegistry().add(record)
            self._info["custom_icon_path"] = dest
            self._update_icon_preview()
            self._status(f"Custom icon set for {self._app_name}")
        except OSError as e:
            QMessageBox.critical(self, "Error", f"Could not set custom icon:\n{e}")

    def _clear_custom_icon(self):
        record = self._get_or_create_record()
        record.custom_icon_path = ""
        InstallationRegistry().add(record)
        self._info["custom_icon_path"] = ""
        self._update_icon_preview()
        self._status(f"Custom icon cleared for {self._app_name}")

    def _update_icon_preview(self):
        icon_path = self._info.get("custom_icon_path") or ""
        if icon_path and os.path.isfile(icon_path):
            pixmap = QPixmap(icon_path)
            if not pixmap.isNull():
                self.custom_icon_preview.setPixmap(pixmap.scaled(24, 24, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                return
        self.custom_icon_preview.clear()

    def _get_or_create_record(self):
        registry = InstallationRegistry()
        record = registry.get(self._app_name)
        if not record:
            from niruvi.installation_registry import InstallationRecord
            record = InstallationRecord(name=self._app_name, path=self._info.get("path", ""), version=self._info.get("version", ""))
        return record

    def _save_run_args(self):
        record = self._get_or_create_record()
        record.run_args = self.run_args_edit.text().strip()
        InstallationRegistry().add(record)
        self._status(f"Run arguments saved for {self._app_name}")

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
        InstallationRegistry().add(record)
        self._status(f"Environment variables saved for {self._app_name}")

    def _save_update_url(self):
        url = self.update_url_edit.text().strip()
        normalized = normalize_update_url(url) if url else ""
        record = self._get_or_create_record()
        record.update_url = normalized
        InstallationRegistry().add(record)
        self._info["update_url"] = normalized
        self._update_source_type_label()
        self._status(f"Update URL saved for {self._app_name}")

    def _update_source_type_label(self):
        url = self.update_url_edit.text().strip()
        if url:
            st = detect_source_type(url)
            labels = {"github": "GitHub Releases", "gitlab": "GitLab Releases", "direct": "Direct URL"}
            self.source_type_label.setText(f"Source: {labels.get(st, st)}")
        else:
            self.source_type_label.setText("")

    def _auto_detect_source(self):
        url = self.update_url_edit.text().strip()
        if not url:
            QMessageBox.information(self, "No URL", "Enter a GitHub or GitLab repository URL first.")
            return
        st = detect_source_type(url)
        if st == "github":
            repo = parse_github_repo(url)
            if repo:
                QMessageBox.information(self, "GitHub Repository Detected",
                    f"Owner: {repo[0]}\nRepo: {repo[1]}\n\nNiruvi will auto-detect the latest release from GitHub when checking for updates.")
            self._update_source_type_label()
        elif st == "gitlab":
            project = parse_gitlab_project(url)
            if project:
                QMessageBox.information(self, "GitLab Project Detected",
                    f"Project: {project}\n\nNiruvi will auto-detect the latest release from GitLab when checking for updates.")
            self._update_source_type_label()
        else:
            QMessageBox.information(self, "Direct URL", "This URL will be used as a direct download link for updates.")
        self._save_update_url()

    def _save_channel(self, channel: str):
        record = self._get_or_create_record()
        record.update_channel = channel
        InstallationRegistry().add(record)

    def _save_auto_update(self, enabled: bool):
        record = self._get_or_create_record()
        record.auto_update = enabled
        InstallationRegistry().add(record)

    def _check_for_updates(self):
        url = self.update_url_edit.text().strip()
        if not url:
            QMessageBox.information(self, "No Update URL", "No update URL configured. Enter a GitHub/GitLab repo URL or direct download link.")
            return
        current_version = self._info.get("version", "")
        if not current_version or current_version == "unknown":
            QMessageBox.information(self, "Unknown Version", "Current version is unknown. Update check requires a known version string.")
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
            msg = f"Version {latest} is available for {self._app_name}.\n\nCurrent: {self._info.get('version', 'unknown')}\nNew: {latest}\n"
            if changelog:
                msg += f"\nWhat's new:\n{changelog[:500]}"
            reply = QMessageBox.question(self, "Update Available", msg + "\n\nDownload and install now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes)
            if reply == QMessageBox.StandardButton.Yes:
                self._download_and_update(download_url, latest)
        else:
            QMessageBox.information(self, "Up to Date", f"{self._app_name} (version {self._info.get('version', '?')}) is already the latest version.")

    def _on_update_check_error(self, error_msg: str):
        self.btn_check_update.setEnabled(True)
        self.btn_check_update.setText("Check for Updates")
        QMessageBox.warning(self, "Update Check Failed", f"Could not check for updates:\n{error_msg}")

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
            record = registry.get(self._app_name)
            if record:
                record.version = version
                registry.add(record)
            QMessageBox.information(self, "Update Complete", f"{self._app_name} has been updated to version {version}.")
            self._status(f"Updated {self._app_name} to version {version}")
        except Exception as e:
            progress.close()
            QMessageBox.critical(self, "Update Failed", f"Failed to update:\n{e}")

    def _run_app(self):
        parent = self.parent()
        if parent and hasattr(parent, "_run_app"):
            parent._run_app(self._app_name)

    def _refresh_gpu_info(self, layout: QVBoxLayout):
        while layout.count() > 0:
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        gpu_info = _get_gpu_info()
        if gpu_info:
            for line in gpu_info:
                lbl = QLabel(line)
                lbl.setWordWrap(True)
                lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                layout.insertWidget(layout.count() - 1, lbl)
        refresh_btn = QPushButton(get_icon("view-refresh"), "Refresh GPU Info")
        refresh_btn.clicked.connect(lambda: self._refresh_gpu_info(layout))
        layout.addWidget(refresh_btn)

    def _uninstall_app(self):
        self.accept()
        parent = self.parent()
        if parent and hasattr(parent, "_uninstall_app"):
            parent._uninstall_app(self._app_name)

    def _status(self, msg: str):
        parent = self.parent()
        if parent and hasattr(parent, "_status_bar"):
            parent._status_bar.showMessage(msg)
