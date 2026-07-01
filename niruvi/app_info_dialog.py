import datetime
import os
import shutil
import tempfile
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QEventLoop, pyqtSignal
from PyQt6.QtGui import QPixmap, QFont
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QFrame, QSizePolicy,
    QPushButton, QTreeWidget, QTreeWidgetItem,
    QLineEdit, QProgressDialog, QMessageBox, QFileDialog,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox,
    QGroupBox, QWidget, QListWidget, QStackedWidget, QListWidgetItem,
)

from niruvi._version import __app_name__
from niruvi.utils import get_icon


_DETAILS_ICONS: dict[str, str] = {
    "Name": "tag",
    "Path": "folder-open",
    "Size": "hard-drive",
    "Installed": "clock",
    "Architecture": "cpu",
    "SHA256": "identification-card",
    "Type": "package-x-generic",
    "Desktop Entry": "document-properties",
    "Shortcut": "user-desktop",
}
from niruvi.toggle_switch import ToggleSwitch
from niruvi.installation_registry import InstallationRegistry
from niruvi.update_sources import (
    resolve_update_source, normalize_update_url,
    detect_source_type, parse_github_repo, parse_gitlab_project,
)
from niruvi.self_update import compare_versions
from niruvi.sandbox import ShieldConfig, SandboxBackend
from niruvi.sandbox import check_firejail_available, check_bwrap_available
from niruvi.sound_manager import play as play_sound

_SIDEBAR_STYLE = """
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
    background: palette(highlight);
    color: palette(highlighted-text);
}
QListWidget::item:hover:!selected {
    background: palette(midlight);
}
"""

_TAB_PAGE_STYLE = """
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


class UpdateCheckWorker(QThread):
    update_checked = pyqtSignal(bool, str, str, str)
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
            self.update_checked.emit(available, info.version, info.download_url, info.changelog or "")
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


_TABS = [
    ("Details", "tag"),
    ("Customization", "preferences-system"),
    ("Isolation", "computer"),
    ("Updates", "emblem-downloads"),
    ("Files", "folder-open"),
]


class AppInfoDialog(QDialog):
    def __init__(self, app_name: str, app_info: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{app_name} — App Info")
        self.setMinimumSize(620, 540)
        self.resize(700, 600)
        self.setModal(True)
        self._app_name = app_name
        self._info = app_info
        self._update_worker = None
        
        self._init_ui()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        registry = InstallationRegistry()
        record = registry.get(self._app_name)
        app_dir = self._info.get("path", "?")

        # ── Header Card ──
        header_card = QWidget()
        header_card.setObjectName("card")
        header_card.setStyleSheet(_CARD_STYLE)
        header_layout = QHBoxLayout(header_card)
        header_layout.setContentsMargins(16, 12, 16, 12)
        header_layout.setSpacing(16)

        icon_label = QLabel()
        icon_path = self._info.get("icon_path")
        if icon_path and os.path.exists(icon_path):
            pixmap = QPixmap(icon_path)
            if not pixmap.isNull():
                icon_label.setPixmap(pixmap.scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            icon_label.setPixmap(get_icon("package-x-generic", "application-x-archive").pixmap(48, 48))
        icon_label.setFixedSize(48, 48)
        header_layout.addWidget(icon_label)

        info_col = QVBoxLayout()
        info_col.setSpacing(2)
        display_name = self._info.get("display_name", self._app_name)
        title = QLabel(display_name)
        tf = title.font()
        tf.setPointSize(16)
        tf.setBold(True)
        title.setFont(tf)
        info_col.addWidget(title)

        version_str = self._info.get("version", "unknown")
        sub = QLabel(f"Version {version_str}  ·  {self._app_name}")
        sub.setStyleSheet("color: palette(disabled-text); font-size: 12px;")
        info_col.addWidget(sub)

        header_layout.addLayout(info_col, 1)
        root.addWidget(header_card)

        # ── Sidebar + Content ──
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setStyleSheet(_SIDEBAR_STYLE)
        self.sidebar.setFixedWidth(160)
        self.sidebar.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.stack = QStackedWidget()

        pages = []
        for i, (label, icon_name) in enumerate(_TABS):
            item = QListWidgetItem(get_icon(icon_name), label)
            self.sidebar.addItem(item)
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(16, 12, 16, 12)
            page_layout.setSpacing(8)
            self.stack.addWidget(page)
            pages.append((page, page_layout))

        body.addWidget(self.sidebar)
        body.addWidget(self.stack, 1)
        root.addLayout(body, 1)

        # ── Tab Pages ──

        # --- Tab 0: Details ---
        details_layout = pages[0][1]
        details_group = QGroupBox("Details")
        details_group.setStyleSheet(_TAB_PAGE_STYLE)
        details_grid = QVBoxLayout(details_group)
        details_grid.setSpacing(6)

        def add_field(label, value):
            row = QHBoxLayout()
            row.setSpacing(8)
            icon_name = _DETAILS_ICONS.get(label, "dialog-information")
            icon_lbl = QLabel()
            icon = get_icon(icon_name)
            if icon and not icon.isNull():
                pixmap = icon.pixmap(16, 16)
                icon_lbl.setPixmap(pixmap)
            icon_lbl.setFixedSize(20, 20)
            icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row.addWidget(icon_lbl)
            lbl = QLabel(label)
            lbl.setFixedWidth(100)
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

        details_grid.addStretch()
        details_layout.addWidget(details_group)

        # --- Tab 1: Customization ---
        cust_layout = pages[1][1]
        cust_group = QGroupBox("Customization")
        cust_group.setStyleSheet(_TAB_PAGE_STYLE)
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

        cust_reset_row = QHBoxLayout()
        self.btn_reset_defaults = QPushButton(get_icon("document-revert"), "Reset to Defaults")
        self.btn_reset_defaults.setToolTip("Reset all customization, updates, and isolation settings to defaults for this app")
        self.btn_reset_defaults.clicked.connect(self._reset_app_defaults)
        cust_reset_row.addWidget(self.btn_reset_defaults)
        cust_reset_row.addStretch()
        cust_grid.addLayout(cust_reset_row)

        cust_layout.addWidget(cust_group)

        # --- Tab 2: Process Isolation ---
        shield_layout = pages[2][1]
        shield_group = QGroupBox("Process Isolation")
        shield_group.setStyleSheet(_TAB_PAGE_STYLE)
        shield_grid = QVBoxLayout(shield_group)
        shield_grid.setSpacing(6)

        self.sb_enabled_cb = QCheckBox("Enable process hardening")
        self.sb_enabled_cb.setToolTip(
            "Applies rlimits, memory locking, ptrace disable, and malloc hardening."
        )
        shield_grid.addWidget(self.sb_enabled_cb)

        self.cb_portable_home = QCheckBox("Portable .home folder")
        self.cb_portable_home.setToolTip(
            "Redirects $HOME to a .home folder next to the app, keeping user data self-contained."
        )
        shield_grid.addWidget(self.cb_portable_home)

        self.cb_portable_config = QCheckBox("Portable .config folder")
        self.cb_portable_config.setToolTip(
            "Redirects $XDG_CONFIG_HOME to a .config folder next to the app, keeping config self-contained."
        )
        shield_grid.addWidget(self.cb_portable_config)

        backend_row = QHBoxLayout()
        backend_row.addWidget(QLabel("Backend:"))
        self.backend_combo = QComboBox()
        self.backend_combo.addItem("Niruvi Shield", SandboxBackend.SHIELD)
        fj_info = check_firejail_available()
        if fj_info.get("available"):
            self.backend_combo.addItem("Firejail", SandboxBackend.FIREJAIL)
        bw_info = check_bwrap_available()
        if bw_info.get("available"):
            self.backend_combo.addItem("Bubblewrap", SandboxBackend.BUBBLEWRAP)
        backend_row.addWidget(self.backend_combo)
        backend_row.addStretch()
        shield_grid.addLayout(backend_row)

        sb_status = QLabel("Process hardening + portable isolation")
        sb_status.setStyleSheet("color: palette(disabled-text); font-size: 11px;")
        shield_grid.addWidget(sb_status)

        sb_btn_row = QHBoxLayout()
        self.btn_save_sandbox = QPushButton(get_icon("document-save"), "Save")
        self.btn_save_sandbox.clicked.connect(self._save_shield_config)
        sb_btn_row.addWidget(self.btn_save_sandbox)
        self.btn_reset_sandbox = QPushButton(get_icon("document-revert"), "Reset to Defaults")
        self.btn_reset_sandbox.clicked.connect(self._reset_shield_defaults)
        sb_btn_row.addWidget(self.btn_reset_sandbox)
        sb_btn_row.addStretch()
        shield_grid.addLayout(sb_btn_row)

        shield_layout.addWidget(shield_group)
        self._load_shield_ui()

        # --- Tab 3: Updates ---
        update_layout = pages[3][1]
        update_group = QGroupBox("Updates")
        update_group.setStyleSheet(_TAB_PAGE_STYLE)
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

        revert_row = QHBoxLayout()
        self.btn_revert = QPushButton("Revert to Previous Version")
        self.btn_revert.setIcon(get_icon("document-revert"))
        prev_dir = app_dir + ".prev"
        self.btn_revert.setEnabled(os.path.isdir(prev_dir))
        self.btn_revert.clicked.connect(self._revert_version)
        revert_row.addWidget(self.btn_revert)
        revert_row.addStretch()
        update_grid.addLayout(revert_row)

        update_layout.addWidget(update_group)

        # --- Tab 4: Files ---
        files_layout = pages[4][1]
        files_group = QGroupBox("Files")
        files_group.setStyleSheet(_TAB_PAGE_STYLE)
        files_inner = QVBoxLayout(files_group)
        if os.path.isdir(app_dir):
            tree = FileTreeWidget(app_dir)
            tree.setMinimumHeight(120)
            files_inner.addWidget(tree, 1)
        else:
            files_inner.addWidget(QLabel("App directory not found."))
        files_layout.addWidget(files_group)

        # Connect sidebar selection
        self.sidebar.currentRowChanged.connect(self._on_tab_changed)
        self.sidebar.setCurrentRow(0)
        # ── Bottom Action Bar ──
        action_bar = QFrame()
        action_bar.setFrameShape(QFrame.Shape.NoFrame)
        action_bar.setStyleSheet("background: palette(window); border-top: 1px solid palette(midlight);")
        action_layout = QHBoxLayout(action_bar)
        action_layout.setContentsMargins(20, 10, 20, 10)
        action_layout.setSpacing(8)

        is_self = (self._app_name == __app_name__)
        if not is_self:
            self.btn_run = QPushButton(get_icon("media-playback-start"), "Run")
            self.btn_run.setStyleSheet("QPushButton { padding: 8px 18px; font-weight: bold; }")
            self.btn_run.clicked.connect(lambda: self._run_app())
            action_layout.addWidget(self.btn_run)

            self.btn_uninstall = QPushButton(get_icon("edit-delete"), "Uninstall")
            self.btn_uninstall.setStyleSheet("QPushButton { padding: 8px 18px; }")
            self.btn_uninstall.clicked.connect(lambda: self._uninstall_app())
            action_layout.addWidget(self.btn_uninstall)

        action_layout.addStretch()

        close_btn = QPushButton(get_icon("dialog-close"), "Close")
        close_btn.setStyleSheet("QPushButton { padding: 8px 18px; }")
        close_btn.clicked.connect(self.accept)
        action_layout.addWidget(close_btn)

        root.addWidget(action_bar)

    def _on_tab_changed(self, index: int):
        self.stack.setCurrentIndex(index)

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
            play_sound("error")
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

    def _revert_version(self):
        """Revert to the .prev version via manager."""
        app_name = self._app_name
        if not app_name:
            return
        prev_dir = self._info.get("path", "") + ".prev"
        if not os.path.isdir(prev_dir):
            QMessageBox.information(self, "No Backup", "No previous version found to revert to.")
            self.btn_revert.setEnabled(False)
            return
        reply = QMessageBox.question(
            self, "Revert Version",
            f"Revert {app_name} to the version in {prev_dir}?\n"
            "The current version will be removed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            app_dir = self._info["path"]
            temp_dir = app_dir + ".tmp_revert"
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
            os.rename(app_dir, temp_dir)
            os.rename(prev_dir, app_dir)
            shutil.rmtree(temp_dir, ignore_errors=True)
            self.btn_revert.setEnabled(os.path.isdir(app_dir + ".prev"))
            QMessageBox.information(self, "Reverted", f"{app_name} has been reverted to the previous version.")
        except OSError as e:
            play_sound("error")
            QMessageBox.critical(self, "Revert Failed", str(e))

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
        self._update_worker.update_checked.connect(self._on_update_check_done)
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
        play_sound("warning")
        QMessageBox.warning(self, "Update Check Failed", f"Could not check for updates:\n{error_msg}")

    def _download_and_update(self, download_url: str, latest_version: str):
        from niruvi.worker import DownloadWorker, extract_appimage_sync
        dest_dir = self._info.get("path", "")
        fd, temp_path = tempfile.mkstemp(suffix=".AppImage")
        os.close(fd)

        progress = QProgressDialog(f"Downloading {self._app_name} update...", "Cancel", 0, 100, self)
        progress.setWindowTitle("Downloading Update")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setAutoClose(True)
        progress.setValue(0)

        worker = DownloadWorker(download_url, temp_path, "", self)
        worker.progress_updated.connect(progress.setValue)
        loop = QEventLoop()
        error_msg = [None]

        def on_finished(_p):
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
        record = registry.get(self._app_name)
        if record:
            record.version = version
            registry.add(record)
        QMessageBox.information(self, "Update Complete", f"{self._app_name} has been updated to version {version}.")
        self._status(f"Updated {self._app_name} to version {version}")

    def _load_shield_ui(self):
        from niruvi.settings import _settings
        record = self._get_or_create_record()
        sc = record.sandbox_config or {}
        default_enabled = _settings.get("sandbox_default_enabled", True)
        default_backend_str = _settings.get("sandbox_default_backend", "shield")
        default_backend = {"shield": SandboxBackend.SHIELD, "firejail": SandboxBackend.FIREJAIL, "bwrap": SandboxBackend.BUBBLEWRAP}.get(default_backend_str, SandboxBackend.SHIELD)
        self.sb_enabled_cb.setChecked(sc.get("enabled", default_enabled))
        self.cb_portable_home.setChecked(sc.get("portable_home", False))
        self.cb_portable_config.setChecked(sc.get("portable_config", False))
        backend = sc.get("backend", default_backend)
        for i in range(self.backend_combo.count()):
            if self.backend_combo.itemData(i) == backend:
                self.backend_combo.setCurrentIndex(i)
                break

    def _save_shield_config(self):
        record = self._get_or_create_record()
        sc = {
            "enabled": self.sb_enabled_cb.isChecked(),
            "hardening": self.sb_enabled_cb.isChecked(),
            "portable_home": self.cb_portable_home.isChecked(),
            "portable_config": self.cb_portable_config.isChecked(),
            "backend": self.backend_combo.currentData(),
        }
        if sc.get("portable_home"):
            Path(os.path.join(record.path, ".home")).mkdir(exist_ok=True)
        if sc.get("portable_config"):
            Path(os.path.join(record.path, ".config")).mkdir(exist_ok=True)
        record.sandbox_config = sc
        InstallationRegistry().add(record)
        self._load_shield_ui()
        self._status(f"Shield config saved for {self._app_name}")

    def _reset_shield_defaults(self):
        sc = ShieldConfig(enabled=True)
        record = self._get_or_create_record()
        new_config = sc.to_dict()
        new_config["portable_home"] = self.cb_portable_home.isChecked()
        new_config["portable_config"] = self.cb_portable_config.isChecked()
        record.sandbox_config = new_config
        InstallationRegistry().add(record)
        self._load_shield_ui()
        self._status(f"Shield reset to defaults for {self._app_name}")

    def _run_app(self):
        parent = self.parent()
        if parent and hasattr(parent, "_run_app"):
            parent._run_app(self._app_name)

    def _uninstall_app(self):
        self.accept()
        parent = self.parent()
        if parent and hasattr(parent, "_uninstall_app"):
            parent._uninstall_app(self._app_name)

    def _reset_app_defaults(self):
        reply = QMessageBox.question(
            self, "Reset to Defaults",
            f"Reset all settings for {self._app_name} to defaults?\n\n"
            "This will clear custom display name, icon, run arguments, "
            "environment variables, update URL/channel, and isolation settings.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        record = self._get_or_create_record()
        record.display_name_override = ""
        record.run_args = ""
        record.env_vars = {}
        record.custom_icon_path = ""
        record.update_url = ""
        record.update_channel = "stable"
        record.auto_update = False
        record.sandbox_config = {"enabled": True, "hardening": True, "portable_home": False, "portable_config": False, "backend": SandboxBackend.SHIELD}
        InstallationRegistry().add(record)
        self.display_name_edit.clear()
        self.run_args_edit.clear()
        self.env_table.setRowCount(0)
        self.update_url_edit.clear()
        self.channel_combo.setCurrentIndex(0)
        self.auto_update_toggle.setChecked(False)
        self._update_icon_preview()
        self._load_shield_ui()
        self._update_source_type_label()
        self._info["custom_icon_path"] = ""
        self._status(f"All settings reset to defaults for {self._app_name}")

    def _status(self, msg: str):
        parent = self.parent()
        if parent and hasattr(parent, "_status_bar"):
            parent._status_bar.showMessage(msg)
