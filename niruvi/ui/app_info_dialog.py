import logging
import os
import shutil
import tempfile
from pathlib import Path

from PyQt6.QtCore import QEventLoop, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from niruvi._version import __app_name__
from niruvi.core.worker import DownloadWorker, start_worker
from niruvi.utils import get_icon, icon_pixmap
from niruvi.utils.styles import format_date, format_size

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
from niruvi.app.self_update import compare_versions
from niruvi.app.update_sources import (
    detect_source_type,
    normalize_update_url,
    parse_github_repo,
    parse_gitlab_project,
    resolve_update_source,
)
from niruvi.core.backends import (
    NOT_SANDBOXED,
    PARTIALLY_RESTRICTED,
    SANDBOXED,
    STATE_LABELS,
    get_backend,
)
from niruvi.core.policy_store import PolicyStore, resolve_policy
from niruvi.desktop.icon_utils import get_pixmap_from_file
from niruvi.desktop.installation_registry import InstallationRegistry
from niruvi.ui.permissions_page import PermissionsPage
from niruvi.ui.security_page import SecurityPage
from niruvi.ui.toggle_switch import ToggleSwitch

from niruvi.utils.sound_manager import play as play_sound
from niruvi.utils.sound_manager import play_and
from niruvi.utils.styles import card_style, tab_page_style
from niruvi.utils.theme_engine import COLOR_ERROR, COLOR_SUCCESS, COLOR_WARNING, style

logger = logging.getLogger(__name__)


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
            available = compare_versions(info.version, "gt", self.current_version)
            self.update_checked.emit(available, info.version, info.download_url, info.changelog or "")
        except Exception as e:
            self.error.emit(str(e))


class FileTreeWidget(QTreeWidget):
    """Lazy-loading directory tree.

    Only the top level is read up front; each folder's children are read when
    it is first expanded. This keeps the dialog responsive on large trees
    instead of walking the entire directory synchronously.
    """

    def __init__(self, app_dir: str, parent=None):
        super().__init__(parent)
        self._app_dir = app_dir
        self.setHeaderLabels(["Name", "Size", "Type", "Modified"])
        self.setColumnWidth(0, 240)
        self.setColumnWidth(1, 90)
        self.setColumnWidth(2, 80)
        self.setAlternatingRowColors(True)
        self.setRootIsDecorated(True)
        self.setUniformRowHeights(True)
        self.setAnimated(False)
        self.itemExpanded.connect(self._on_item_expanded)
        self.refresh()

    def refresh(self):
        self.clear()
        for name in self._list_entries(self._app_dir):
            self._make_item(None, os.path.join(self._app_dir, name), name)

    @staticmethod
    def _list_entries(path: str) -> list[str]:
        try:
            names = os.listdir(path)
        except OSError:
            return []
        return sorted(names, key=lambda n: (not os.path.isdir(os.path.join(path, n)), n.lower()))

    def _make_item(self, parent_item, full_path: str, name: str) -> QTreeWidgetItem:
        is_dir = os.path.isdir(full_path)
        parent = parent_item if parent_item is not None else self.invisibleRootItem()
        item = QTreeWidgetItem(parent)
        item.setText(0, name + "/" if is_dir else name)
        item.setData(0, Qt.ItemDataRole.UserRole, full_path)
        item.setData(0, Qt.ItemDataRole.UserRole + 1, is_dir)
        try:
            mtime = os.path.getmtime(full_path)
            item.setText(3, format_date(mtime))
        except OSError:
            pass
        if is_dir:
            item.setText(2, "Folder")
            self._add_placeholder(item)
        else:
            item.setText(2, "File")
            try:
                item.setText(1, format_size(os.path.getsize(full_path)))
            except OSError:
                item.setText(1, "")
        return item

    def _add_placeholder(self, item: QTreeWidgetItem):
        placeholder = QTreeWidgetItem(item)
        placeholder.setText(0, "Loading…")
        placeholder.setData(0, Qt.ItemDataRole.UserRole, None)

    def _on_item_expanded(self, item: QTreeWidgetItem):
        # A single child with no path is either the loading placeholder or the
        # "(empty)" marker; replace it with the real listing on first expand.
        if item.childCount() != 1:
            return
        child = item.child(0)
        if child.data(0, Qt.ItemDataRole.UserRole) is not None:
            return
        item.removeChild(child)
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path and os.path.isdir(path):
            for name in self._list_entries(path):
                self._make_item(item, os.path.join(path, name), name)
        if item.childCount() == 0:
            empty = QTreeWidgetItem(item)
            empty.setText(0, "(empty)")
            empty.setData(0, Qt.ItemDataRole.UserRole, None)


class DirectoryStatsWorker(QThread):
    """Counts files and total bytes off the UI thread."""

    done = pyqtSignal(int, int)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._path = path

    def run(self):
        count = 0
        total = 0
        try:
            for dirpath, _dirs, files in os.walk(self._path):
                for f in files:
                    count += 1
                    try:
                        total += os.path.getsize(os.path.join(dirpath, f))
                    except OSError:
                        pass
        except OSError:
            pass
        self.done.emit(count, total)


class StorageStatsWorker(QThread):
    """Runs the core storage analysis off the UI thread."""

    done = pyqtSignal(object)  # StorageReport

    def __init__(self, app_name: str, app_dir: str, parent=None):
        super().__init__(parent)
        self._app_name = app_name
        self._app_dir = app_dir

    def run(self):
        from niruvi.core.storage import analyze_storage

        try:
            report = analyze_storage([(self._app_name, self._app_dir)])
        except Exception as e:
            logger.debug("Storage analysis failed for %s: %s", self._app_name, e)
            report = None
        self.done.emit(report)


# Advanced tab index used by callers (`open on Settings tab` etc.)
_TABS = [
    ("Overview", "tag"),
    ("Permissions", "security-high"),
    ("Security", "security-high"),
    ("Storage", "hard-drive"),
    ("Updates", "emblem-downloads"),
    ("Advanced", "preferences-system"),
]


class AppInfoDialog(QDialog):
    def __init__(self, app_name: str, app_info: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{app_name} — App Info")
        self.setMinimumSize(560, 480)
        self.resize(720, 560)
        self.setModal(True)
        self._app_name = app_name
        self._info = app_info
        self._update_worker: UpdateCheckWorker | None = None
        self._download_worker: DownloadWorker | None = None

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
        header_card.setStyleSheet(card_style())
        header_layout = QHBoxLayout(header_card)
        header_layout.setContentsMargins(16, 12, 16, 12)
        header_layout.setSpacing(16)

        icon_label = QLabel()
        icon_path = self._info.get("icon_path")
        pixmap = get_pixmap_from_file(icon_path, 48) if icon_path else None
        if pixmap is not None:
            icon_label.setPixmap(pixmap)
        else:
            icon_label.setPixmap(icon_pixmap(get_icon("package-x-generic", "application-x-archive"), 48))
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
        title.setWordWrap(True)
        title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        info_col.addWidget(title)

        version_str = self._info.get("version", "unknown")
        sub = QLabel(f"Version {version_str}  ·  {self._app_name}")
        sub.setStyleSheet(style("color: {colors.subtle}; font-size: 12px;"))
        sub.setWordWrap(True)
        info_col.addWidget(sub)

        self._header_chip = QLabel()
        self._header_chip.setStyleSheet(style("font-size: 11px;"))
        info_col.addWidget(self._header_chip)

        header_layout.addLayout(info_col, 1)
        root.addWidget(header_card)
        self._update_header_chip(record)

        # ── Main Content Area ──
        # Use a scroll area for the whole content to handle small screens
        content_scroll = QScrollArea()
        content_scroll.setWidgetResizable(True)
        content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        root.addWidget(content_scroll, 1)

        content_widget = QWidget()
        content_scroll.setWidget(content_widget)
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(12, 8, 12, 8)
        content_layout.setSpacing(8)

        # Tab Bar (horizontal, responsive)
        self.tab_bar = QWidget()
        tab_bar_layout = QHBoxLayout(self.tab_bar)
        tab_bar_layout.setContentsMargins(0, 0, 0, 0)
        tab_bar_layout.setSpacing(4)
        tab_bar_layout.addStretch()

        self.tab_buttons = []
        for i, (label, icon_name) in enumerate(_TABS):
            btn = QPushButton(get_icon(icon_name), label)
            btn.setCheckable(True)
            btn.setFlat(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            if i == 0:
                btn.setChecked(True)
            btn.clicked.connect(lambda checked, idx=i: self._on_tab_button_clicked(idx))
            btn.setToolTip(label)
            # Make buttons take minimum space, wrap on small screens
            btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            tab_bar_layout.addWidget(btn)
            self.tab_buttons.append(btn)
        tab_bar_layout.addStretch()
        content_layout.addWidget(self.tab_bar)

        # Stacked widget for tab content (no nested scroll areas)
        self.stack = QStackedWidget()
        self.stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        pages = []
        for _i in range(len(_TABS)):
            page = QWidget()
            page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(0, 8, 0, 0)
            page_layout.setSpacing(8)
            self.stack.addWidget(page)
            pages.append(page)

        content_layout.addWidget(self.stack, 1)
        self.stack.setCurrentIndex(0)

        # Default: select first tab
        self.tab_buttons[0].setChecked(True)

        # ── Tab Pages ──

        # --- Tab 0: Details ---
        details_layout = pages[0].layout()
        details_group = QGroupBox("Details")
        details_group.setStyleSheet(tab_page_style())
        details_grid = QVBoxLayout(details_group)
        details_grid.setSpacing(6)

        def add_field(label, value):
            row = QHBoxLayout()
            row.setSpacing(8)
            icon_name = _DETAILS_ICONS.get(label, "dialog-information")
            icon_lbl = QLabel()
            icon = get_icon(icon_name)
            if icon and not icon.isNull():
                pixmap = icon_pixmap(icon, 16)
                icon_lbl.setPixmap(pixmap)
            icon_lbl.setFixedSize(20, 20)
            icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row.addWidget(icon_lbl)
            lbl = QLabel(label)
            lbl.setFixedWidth(100)
            lbl.setStyleSheet(style("font-weight: bold; color: {colors.subtle}; font-size: 12px;"))
            val = QLabel(str(value))
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            val.setWordWrap(True)
            val.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            row.addWidget(lbl)
            row.addWidget(val, 1)
            details_grid.addLayout(row)
            return val

        add_field("Name", self._app_name)
        add_field("Path", app_dir)
        if os.path.isdir(app_dir):
            cached_size = (record and record.size) or 0
            if cached_size > 0:
                add_field("Size", format_size(cached_size))
            else:
                # Walk off the main thread and fill the row when it arrives:
                # a synchronous scan here would freeze the dialog on slow disks.
                size_val = add_field("Size", "Calculating…")
                self._size_worker = DirectoryStatsWorker(app_dir)
                self._size_worker.done.connect(
                    lambda count, total: size_val.setText(format_size(total) if total else "—")
                )
                start_worker(self._size_worker)
            add_field("Installed", format_date(os.path.getctime(app_dir)))
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

        # --- Regular: Tab 4 Advanced -> Customization block ---
        cust_layout = pages[4].layout()
        cust_group = QGroupBox("Customization")
        cust_group.setStyleSheet(tab_page_style())
        cust_form = QFormLayout(cust_group)
        cust_form.setSpacing(10)
        cust_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        cust_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        cust_form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        # Display name
        self.display_name_edit = QLineEdit()
        self.display_name_edit.setPlaceholderText("Override name (leave empty for default)")
        if record and record.display_name_override:
            self.display_name_edit.setText(record.display_name_override)
        self.btn_save_display_name = QPushButton(get_icon("document-save"), "Save")
        self.btn_save_display_name.clicked.connect(self._save_display_name)
        name_widget = QWidget()
        name_layout = QHBoxLayout(name_widget)
        name_layout.setContentsMargins(0, 0, 0, 0)
        name_layout.addWidget(self.display_name_edit, 1)
        name_layout.addWidget(self.btn_save_display_name)
        cust_form.addRow("Display name:", name_widget)

        # Tags / category
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("e.g. Media, Video (comma separated)")
        from niruvi.app.tags import infer_tags

        record_tags = (record.tags if record and record.tags else []) or infer_tags(self._app_name)
        if record_tags:
            self.tags_edit.setText(", ".join(record_tags))
        self.btn_save_tags = QPushButton(get_icon("document-save"), "Save")
        self.btn_save_tags.clicked.connect(self._save_tags)
        tags_widget = QWidget()
        tags_layout = QHBoxLayout(tags_widget)
        tags_layout.setContentsMargins(0, 0, 0, 0)
        tags_layout.addWidget(self.tags_edit, 1)
        tags_layout.addWidget(self.btn_save_tags)
        cust_form.addRow("Tags / category:", tags_widget)

        # Custom icon
        self.custom_icon_preview = QLabel()
        self.custom_icon_preview.setFixedSize(24, 24)
        self.btn_pick_icon = QPushButton(get_icon("image-x-generic"), "Choose...")
        self.btn_pick_icon.clicked.connect(self._pick_custom_icon)
        self.btn_clear_icon = QPushButton(get_icon("edit-delete"), "Clear")
        self.btn_clear_icon.clicked.connect(self._clear_custom_icon)
        icon_widget = QWidget()
        icon_layout = QHBoxLayout(icon_widget)
        icon_layout.setContentsMargins(0, 0, 0, 0)
        icon_layout.addWidget(self.custom_icon_preview)
        icon_layout.addWidget(self.btn_pick_icon)
        icon_layout.addWidget(self.btn_clear_icon)
        icon_layout.addStretch()
        self._update_icon_preview()
        cust_form.addRow("Custom icon:", icon_widget)

        # Run arguments
        self.run_args_edit = QLineEdit()
        self.run_args_edit.setPlaceholderText("e.g. --verbose --config=myconfig.conf")
        if record and record.run_args:
            self.run_args_edit.setText(record.run_args)
        self.btn_save_run_args = QPushButton(get_icon("document-save"), "Save")
        self.btn_save_run_args.clicked.connect(self._save_run_args)
        args_widget = QWidget()
        args_layout = QHBoxLayout(args_widget)
        args_layout.setContentsMargins(0, 0, 0, 0)
        args_layout.addWidget(self.run_args_edit, 1)
        args_layout.addWidget(self.btn_save_run_args)
        cust_form.addRow("Run arguments:", args_widget)

        # Environment variables
        self.env_table = QTableWidget()
        self.env_table.setColumnCount(2)
        self.env_table.setHorizontalHeaderLabels(["Variable", "Value"])
        header = self.env_table.horizontalHeader()
        if header is not None:
            header.setStretchLastSection(True)
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.env_table.setMinimumHeight(120)
        self.env_table.setMaximumHeight(200)
        self.env_table.setAlternatingRowColors(True)
        self.env_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.env_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        if record and record.env_vars:
            self._populate_env_table(record.env_vars)
        cust_form.addRow("Environment variables:", self.env_table)

        env_btn_row = QHBoxLayout()
        self.btn_add_env = QPushButton(get_icon("list-add"), "Add")
        self.btn_add_env.clicked.connect(lambda: play_and("click", self._add_env_row))
        env_btn_row.addWidget(self.btn_add_env)
        self.btn_remove_env = QPushButton(get_icon("list-remove"), "Remove Selected")
        self.btn_remove_env.clicked.connect(lambda: play_and("click", self._remove_env_row))
        env_btn_row.addWidget(self.btn_remove_env)
        self.btn_save_env = QPushButton(get_icon("document-save"), "Save")
        self.btn_save_env.clicked.connect(self._save_env_vars)
        env_btn_row.addWidget(self.btn_save_env)
        env_btn_row.addStretch()
        cust_form.addRow("", env_btn_row)

        # Reset to Defaults (customization only)
        cust_reset_row = QHBoxLayout()
        self.btn_reset_customization = QPushButton(get_icon("document-revert"), "Reset Customization")
        self.btn_reset_customization.setToolTip(
            "Reset display name, icon, run arguments, environment variables, and tags to defaults"
        )
        self.btn_reset_customization.clicked.connect(self._reset_customization)
        cust_reset_row.addWidget(self.btn_reset_customization)
        cust_reset_row.addStretch()
        cust_form.addRow("", cust_reset_row)

        cust_layout.addWidget(cust_group)

        # --- Regular: Tab 1 Permissions ---
        perm_layout = pages[1].layout()
        self._permissions_page = PermissionsPage(self._app_name, self._info)
        self._permissions_page.policy_changed.connect(self._on_policy_changed)
        perm_layout.addWidget(self._permissions_page)

        # --- Regular: Tab 1b Security ---
        security_layout = pages[2].layout()
        self._security_page = SecurityPage(self._app_name, self._info, self)
        security_layout.addWidget(self._security_page.page)

        # --- Regular: Tab 3 Storage ---
        storage_layout = pages[3].layout()
        storage_group = QGroupBox("Storage")
        storage_group.setStyleSheet(tab_page_style())
        self._storage_inner = QVBoxLayout(storage_group)
        self._storage_inner.setSpacing(6)

        self._storage_summary = QLabel("Calculating…")
        self._storage_summary.setWordWrap(True)
        self._storage_summary.setStyleSheet(style("color: {colors.subtle}; font-size: 11px;"))
        self._storage_inner.addWidget(self._storage_summary)

        # Breakdown rows are filled in by _on_storage_report (worker thread).
        self._storage_rows = QVBoxLayout()
        self._storage_rows.setSpacing(4)
        self._storage_inner.addLayout(self._storage_rows)

        storage_btn_row = QHBoxLayout()
        self.btn_cleanup_temp = QPushButton(
            get_icon("view-restore", "edit-clear"), "Clean Up Temporary Files"
        )
        self.btn_cleanup_temp.setToolTip(
            "Remove Niruvi temporary files and staging folders left behind by crashed or interrupted runs."
        )
        self.btn_cleanup_temp.clicked.connect(self._cleanup_temp)
        storage_btn_row.addWidget(self.btn_cleanup_temp)
        self.btn_remove_prev = QPushButton(get_icon("document-revert"), "Delete Rollback Copy")
        self.btn_remove_prev.setToolTip(
            "After this the app can no longer be reverted to the version before the last update."
        )
        self.btn_remove_prev.clicked.connect(self._remove_rollback)
        storage_btn_row.addWidget(self.btn_remove_prev)
        storage_btn_row.addStretch()
        self._storage_inner.addLayout(storage_btn_row)

        storage_layout.addWidget(storage_group)
        storage_layout.addStretch()

        self._storage_worker = StorageStatsWorker(self._app_name, app_dir)
        self._storage_worker.done.connect(self._on_storage_report)
        start_worker(self._storage_worker)


        update_layout = pages[4].layout()

        # Update source
        source_group = QGroupBox("Update source")
        source_group.setStyleSheet(tab_page_style())
        source_v = QVBoxLayout(source_group)
        source_v.setSpacing(6)

        url_row = QHBoxLayout()
        url_row.setSpacing(6)
        self.update_url_edit = QLineEdit()
        self.update_url_edit.setPlaceholderText("GitHub/GitLab repo URL or direct download link")
        if record and record.update_url:
            self.update_url_edit.setText(record.update_url)
        url_row.addWidget(self.update_url_edit, 1)
        self.btn_save_url = QPushButton(get_icon("document-save"), "Save")
        self.btn_save_url.clicked.connect(self._save_update_url)
        url_row.addWidget(self.btn_save_url)
        source_v.addLayout(url_row)

        self.source_type_label = QLabel("")
        self.source_type_label.setStyleSheet(style("color: {colors.subtle}; font-size: 11px;"))
        source_v.addWidget(self.source_type_label)

        source_btn_row = QHBoxLayout()
        self.btn_detect_source = QPushButton(get_icon("network-server"), "Auto-detect Source")
        self.btn_detect_source.setToolTip("Detect whether the URL is a GitHub repo, GitLab project, or direct link")
        self.btn_detect_source.clicked.connect(self._auto_detect_source)
        source_btn_row.addWidget(self.btn_detect_source)
        self.btn_check_update = QPushButton(get_icon("emblem-downloads"), "Check for Updates")
        self.btn_check_update.setToolTip("Check if a newer version is available")
        self.btn_check_update.clicked.connect(self._check_for_updates)
        source_btn_row.addWidget(self.btn_check_update)
        source_btn_row.addStretch()
        source_v.addLayout(source_btn_row)

        self.update_status_label = QLabel("")
        self.update_status_label.setWordWrap(True)
        self.update_status_label.setStyleSheet(style("color: {colors.subtle}; font-size: 11px;"))
        source_v.addWidget(self.update_status_label)

        update_layout.addWidget(source_group)
        self._update_source_type_label()

        # Update channel
        channel_group = QGroupBox("Update channel")
        channel_group.setStyleSheet(tab_page_style())
        channel_form = QFormLayout(channel_group)
        channel_form.setSpacing(8)
        channel_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.channel_combo = QComboBox()
        self.channel_combo.setMinimumWidth(160)
        self.channel_combo.addItems(["stable", "beta", "nightly"])
        if record and record.update_channel:
            idx = self.channel_combo.findText(record.update_channel)
            if idx >= 0:
                self.channel_combo.setCurrentIndex(idx)
        self.channel_combo.currentTextChanged.connect(self._save_channel)
        channel_form.addRow("Channel:", self.channel_combo)
        channel_desc = QLabel(
            "stable = released versions · beta = pre-releases · nightly = newest builds. "
            "Choose the channel to follow when checking for updates."
        )
        channel_desc.setWordWrap(True)
        channel_desc.setStyleSheet(style("color: {colors.subtle}; font-size: 11px;"))
        channel_form.addRow("", channel_desc)
        update_layout.addWidget(channel_group)

        # Automatic updates
        auto_group = QGroupBox("Automatic updates")
        auto_group.setStyleSheet(tab_page_style())
        auto_v = QVBoxLayout(auto_group)
        auto_v.setSpacing(4)
        auto_box = QHBoxLayout()
        auto_box.setSpacing(8)
        self.auto_update_toggle = ToggleSwitch(self)
        if record and record.auto_update:
            self.auto_update_toggle.setChecked(True)
        self.auto_update_toggle.toggled.connect(self._save_auto_update)
        auto_box.addWidget(self.auto_update_toggle)
        auto_box.addWidget(QLabel("Check for updates in the background"))
        auto_box.addStretch()
        auto_v.addLayout(auto_box)
        auto_desc = QLabel(
            "When enabled, Niruvi checks this app for new versions in the background and notifies you "
            "before downloading anything. Updates are never installed without confirmation."
        )
        auto_desc.setWordWrap(True)
        auto_desc.setStyleSheet(style("color: {colors.subtle}; font-size: 11px;"))
        auto_v.addWidget(auto_desc)
        update_layout.addWidget(auto_group)

        # Rollback
        rollback_group = QGroupBox("Rollback")
        rollback_group.setStyleSheet(tab_page_style())
        rollback_v = QVBoxLayout(rollback_group)
        rollback_v.setSpacing(4)
        self.btn_revert = QPushButton(get_icon("document-revert"), "Revert to Previous Version")
        prev_dir = app_dir + ".prev"
        has_prev = os.path.isdir(prev_dir)
        self.btn_revert.setEnabled(has_prev)
        self.btn_revert.clicked.connect(self._revert_version)
        rollback_row = QHBoxLayout()
        rollback_row.addWidget(self.btn_revert)
        rollback_row.addStretch()
        rollback_v.addLayout(rollback_row)
        rollback_desc = QLabel(
            "Restores the backup kept at the previous version. "
            "Niruvi creates this backup automatically before each update."
        )
        rollback_desc.setWordWrap(True)
        rollback_desc.setStyleSheet(style("color: {colors.subtle}; font-size: 11px;"))
        rollback_v.addWidget(rollback_desc)
        if not has_prev:
            rollback_desc.setText(
                "No previous version backup is available yet. "
                "A backup is created automatically before each update."
            )
        update_layout.addWidget(rollback_group)

        update_layout.addStretch()

        # --- Regular: Tab 4 Advanced -> Files block ---
        files_layout = pages[5].layout()
        files_group = QGroupBox("Files")
        files_group.setStyleSheet(tab_page_style())
        files_inner = QVBoxLayout(files_group)
        files_inner.setSpacing(6)

        if os.path.isdir(app_dir):
            files_toolbar = QHBoxLayout()
            self.btn_files_refresh = QPushButton(get_icon("view-refresh"), "Refresh")
            self.btn_files_refresh.setToolTip("Re-read the directory contents")
            self.btn_files_refresh.clicked.connect(lambda: play_and("click", self._refresh_files))
            files_toolbar.addWidget(self.btn_files_refresh)
            self.btn_files_collapse = QPushButton(get_icon("go-up"), "Collapse All")
            self.btn_files_collapse.clicked.connect(lambda: play_and("click", self.file_tree.collapseAll))
            files_toolbar.addWidget(self.btn_files_collapse)
            files_toolbar.addStretch()
            files_inner.addLayout(files_toolbar)

            self.file_tree = FileTreeWidget(app_dir)
            self.file_tree.setMinimumHeight(160)
            files_inner.addWidget(self.file_tree, 1)

            self.files_status = QLabel()
            self.files_status.setWordWrap(True)
            self.files_status.setStyleSheet(style("color: {colors.subtle}; font-size: 11px;"))
            files_inner.addWidget(self.files_status)
            self._update_files_status(app_dir)
        else:
            missing = QLabel(f"App directory not found:\n{app_dir}")
            missing.setWordWrap(True)
            missing.setStyleSheet(style("color: {colors.subtle}; font-size: 12px;"))
            files_inner.addWidget(missing)
            files_inner.addStretch()

        files_layout.addWidget(files_group)

        # ── Action Bar (fixed at the bottom, always visible) ──
        action_bar = QFrame()
        action_bar.setFrameShape(QFrame.Shape.NoFrame)
        action_bar.setObjectName("actionBar")
        # NOTE: scoped via #actionBar — bare declarations would cascade to
        # child buttons and clobber their variant backgrounds.
        action_bar.setStyleSheet(
            style("#actionBar { background-color: {colors.background}; border-top: 1px solid {colors.border}; }")
        )
        action_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        action_layout = QHBoxLayout(action_bar)
        action_layout.setContentsMargins(20, 10, 20, 10)
        action_layout.setSpacing(8)

        is_self = self._app_name == __app_name__
        if not is_self:
            self.btn_run = QPushButton(get_icon("media-playback-start"), "Run")
            self.btn_run.setProperty("variant", "primary")
            self.btn_run.clicked.connect(lambda: play_and("click", self._run_app))
            action_layout.addWidget(self.btn_run)

            self.btn_open_dir = QPushButton(get_icon("folder-open"), "Open Installation Directory")
            self.btn_open_dir.setToolTip("Show this app's install folder in the file manager")
            self.btn_open_dir.clicked.connect(lambda: play_and("click", self._open_install_dir))
            action_layout.addWidget(self.btn_open_dir)

            self.btn_uninstall = QPushButton(get_icon("edit-delete"), "Uninstall")
            self.btn_uninstall.setProperty("variant", "danger")
            self.btn_uninstall.clicked.connect(lambda: play_and("click", self._uninstall_app))
            action_layout.addWidget(self.btn_uninstall)

        action_layout.addStretch()

        close_btn = QPushButton(get_icon("dialog-close"), "Close")
        close_btn.clicked.connect(lambda: play_and("navigation", self.accept))
        action_layout.addWidget(close_btn)

        root.addWidget(action_bar)

    def _on_tab_button_clicked(self, index: int):
        play_sound("navigation")
        self.stack.setCurrentIndex(index)
        for i, btn in enumerate(self.tab_buttons):
            btn.setChecked(i == index)

    def accept(self):
        try:
            InstallationRegistry().flush()
        except Exception as e:
            logger.debug("Registry flush on accept failed: %s", e, exc_info=True)
        super().accept()

    def reject(self):
        try:
            InstallationRegistry().flush()
        except Exception as e:
            logger.debug("Registry flush on reject failed: %s", e, exc_info=True)
        super().reject()

    def _save_display_name(self):
        play_sound("click")
        try:
            override = self.display_name_edit.text().strip()
            record = self._get_or_create_record()
            record.display_name_override = override
            InstallationRegistry().add(record)
            self._info["display_name"] = override or self._app_name
            self._status(f"Display name saved for {self._app_name}")
            play_sound("success")
        except Exception as e:
            logger.debug("Failed to save display name for %s: %s", self._app_name, e, exc_info=True)
            play_sound("error")
            QMessageBox.critical(self, "Save Failed", f"Could not save display name:\n{e}")

    def _pick_custom_icon(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose Custom Icon",
            "",
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
            play_sound("success")
            self._status(f"Custom icon set for {self._app_name}")
        except OSError as e:
            play_sound("error")
            QMessageBox.critical(self, "Error", f"Could not set custom icon:\n{e}")

    def _clear_custom_icon(self):
        play_sound("click")
        try:
            record = self._get_or_create_record()
            record.custom_icon_path = ""
            InstallationRegistry().add(record)
            self._info["custom_icon_path"] = ""
            self._update_icon_preview()
            self._status(f"Custom icon cleared for {self._app_name}")
        except Exception as e:
            logger.debug("Failed to clear custom icon for %s: %s", self._app_name, e, exc_info=True)
            play_sound("error")
            QMessageBox.critical(self, "Save Failed", f"Could not clear custom icon:\n{e}")

    def _update_icon_preview(self):
        icon_path = self._info.get("custom_icon_path") or ""
        if icon_path and os.path.isfile(icon_path):
            pixmap = QPixmap(icon_path)
            if not pixmap.isNull():
                self.custom_icon_preview.setPixmap(
                    pixmap.scaled(
                        24, 24, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                    )
                )
                return
        self.custom_icon_preview.clear()

    def _get_or_create_record(self):
        registry = InstallationRegistry()
        record = registry.get(self._app_name)
        if not record:
            from niruvi.desktop.installation_registry import InstallationRecord

            record = InstallationRecord(
                name=self._app_name, path=self._info.get("path", ""), version=self._info.get("version", "")
            )
        return record

    def _save_run_args(self):
        play_sound("click")
        try:
            record = self._get_or_create_record()
            record.run_args = self.run_args_edit.text().strip()
            InstallationRegistry().add(record)
            self._status(f"Run arguments saved for {self._app_name}")
            play_sound("success")
        except Exception as e:
            logger.debug("Failed to save run args for %s: %s", self._app_name, e, exc_info=True)
            play_sound("error")
            QMessageBox.critical(self, "Save Failed", f"Could not save run arguments:\n{e}")

    def _save_tags(self):
        play_sound("click")
        try:
            from niruvi.app.tags import normalize_tags

            raw = [t.strip() for t in self.tags_edit.text().split(",") if t.strip()]
            record = self._get_or_create_record()
            record.tags = normalize_tags(raw)
            InstallationRegistry().add(record)
            self._status(f"Tags saved for {self._app_name}: {', '.join(record.tags) or 'none'}")
            play_sound("success")
        except Exception as e:
            logger.debug("Failed to save tags for %s: %s", self._app_name, e, exc_info=True)
            play_sound("error")
            QMessageBox.critical(self, "Save Failed", f"Could not save tags:\n{e}")

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
        play_sound("click")
        try:
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
            play_sound("success")
        except Exception as e:
            logger.debug("Failed to save env vars for %s: %s", self._app_name, e, exc_info=True)
            play_sound("error")
            QMessageBox.critical(self, "Save Failed", f"Could not save environment variables:\n{e}")

    def _save_update_url(self):
        play_sound("click")
        try:
            url = self.update_url_edit.text().strip()
            normalized = normalize_update_url(url) if url else ""
            record = self._get_or_create_record()
            record.update_url = normalized
            InstallationRegistry().add(record)
            self._info["update_url"] = normalized
            self._update_source_type_label()
            self._status(f"Update URL saved for {self._app_name}")
            play_sound("success")
        except Exception as e:
            logger.debug("Failed to save update URL for %s: %s", self._app_name, e, exc_info=True)
            play_sound("error")
            QMessageBox.critical(self, "Save Failed", f"Could not save update URL:\n{e}")

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
            play_sound("warning")
            QMessageBox.information(self, "No URL", "Enter a GitHub or GitLab repository URL first.")
            return
        st = detect_source_type(url)
        if st == "github":
            repo = parse_github_repo(url)
            if repo:
                play_sound("info")
                QMessageBox.information(
                    self,
                    "GitHub Repository Detected",
                    f"Owner: {repo[0]}\nRepo: {repo[1]}\n\nNiruvi will auto-detect the latest release from GitHub when checking for updates.",
                )
            self._update_source_type_label()
        elif st == "gitlab":
            project = parse_gitlab_project(url)
            if project:
                play_sound("info")
                QMessageBox.information(
                    self,
                    "GitLab Project Detected",
                    f"Project: {project}\n\nNiruvi will auto-detect the latest release from GitLab when checking for updates.",
                )
            self._update_source_type_label()
        else:
            play_sound("info")
            QMessageBox.information(self, "Direct URL", "This URL will be used as a direct download link for updates.")
        self._save_update_url()

    def _save_channel(self, channel: str):
        play_sound("click")
        try:
            record = self._get_or_create_record()
            record.update_channel = channel
            InstallationRegistry().add(record)
        except Exception as e:
            logger.debug("Failed to save channel for %s: %s", self._app_name, e, exc_info=True)
            play_sound("error")
            QMessageBox.critical(self, "Save Failed", f"Could not save update channel:\n{e}")

    def _save_auto_update(self, enabled: bool):
        try:
            record = self._get_or_create_record()
            record.auto_update = enabled
            InstallationRegistry().add(record)
        except Exception as e:
            logger.debug("Failed to save auto-update flag for %s: %s", self._app_name, e, exc_info=True)
            play_sound("error")
            QMessageBox.critical(self, "Save Failed", f"Could not save auto-update setting:\n{e}")

    def _revert_version(self):
        """Revert to the .prev version via manager."""
        app_name = self._app_name
        if not app_name:
            return
        prev_dir = self._info.get("path", "") + ".prev"
        if not os.path.isdir(prev_dir):
            play_sound("info")
            QMessageBox.information(self, "No Backup", "No previous version found to revert to.")
            self.btn_revert.setEnabled(False)
            return
        play_sound("warning")
        reply = QMessageBox.question(
            self,
            "Revert Version",
            f"Revert {app_name} to the version in {prev_dir}?\nThe current version will be removed.",
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
            play_sound("success")
            QMessageBox.information(self, "Reverted", f"{app_name} has been reverted to the previous version.")
        except OSError as e:
            play_sound("error")
            QMessageBox.critical(self, "Revert Failed", str(e))

    def _check_for_updates(self):
        url = self.update_url_edit.text().strip()
        if not url:
            play_sound("warning")
            QMessageBox.information(
                self,
                "No Update URL",
                "No update URL configured. Enter a GitHub/GitLab repo URL or direct download link.",
            )
            return
        current_version = self._info.get("version", "")
        if not current_version or current_version == "unknown":
            play_sound("warning")
            QMessageBox.information(
                self, "Unknown Version", "Current version is unknown. Update check requires a known version string."
            )
            return
        self.btn_check_update.setEnabled(False)
        self.btn_check_update.setText("Checking...")
        normalized = normalize_update_url(url)
        self._update_worker = UpdateCheckWorker(normalized, current_version)
        self._update_worker.update_checked.connect(self._on_update_check_done)
        self._update_worker.error.connect(self._on_update_check_error)
        start_worker(self._update_worker)

    def _on_update_check_done(self, available: bool, latest: str, download_url: str, changelog: str):
        self.btn_check_update.setEnabled(True)
        self.btn_check_update.setText("Check for Updates")
        current = self._info.get("version", "unknown")
        if available:
            self.update_status_label.setText(f"Update available: {latest} (current: {current}).")
            play_sound("notification")
            msg = f"Version {latest} is available for {self._app_name}.\n\nCurrent: {self._info.get('version', 'unknown')}\nNew: {latest}\n"
            if changelog:
                msg += f"\nWhat's new:\n{changelog[:500]}"
            reply = QMessageBox.question(
                self,
                "Update Available",
                msg + "\n\nDownload and install now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._download_and_update(download_url, latest)
        else:
            self.update_status_label.setText(f"Up to date — version {current} is the latest.")
            play_sound("info")
            QMessageBox.information(
                self,
                "Up to Date",
                f"{self._app_name} (version {self._info.get('version', '?')}) is already the latest version.",
            )

    def _on_update_check_error(self, error_msg: str):
        self.btn_check_update.setEnabled(True)
        self.btn_check_update.setText("Check for Updates")
        self.update_status_label.setText(f"Update check failed: {error_msg}")
        play_sound("warning")
        QMessageBox.warning(self, "Update Check Failed", f"Could not check for updates:\n{error_msg}")

    def _download_and_update(self, download_url: str, latest_version: str):
        from niruvi.core.worker import DownloadWorker, extract_appimage_sync, start_worker

        dest_dir = self._info.get("path", "")
        fd, temp_path = tempfile.mkstemp(suffix=".AppImage")
        os.close(fd)

        progress = QProgressDialog(f"Downloading {self._app_name} update...", "Cancel", 0, 100, self)
        progress.setWindowTitle("Downloading Update")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setAutoClose(True)
        progress.setValue(0)

        self._download_worker = DownloadWorker(download_url, temp_path, "", self)
        self._download_worker.progress_updated.connect(progress.setValue)
        loop = QEventLoop()
        error_msg = [None]

        def on_finished(_p):
            loop.quit()

        def on_error(e):
            error_msg[0] = e
            loop.quit()

        self._download_worker.finished.connect(on_finished)
        self._download_worker.error.connect(on_error)
        start_worker(self._download_worker)
        progress.canceled.connect(self._download_worker.cancel)
        loop.exec()
        progress.close()
        if self._download_worker is not None and self._download_worker.isRunning():
            self._download_worker.wait(5000)
        self._download_worker = None

        if progress.wasCanceled() or error_msg[0] == "cancelled":
            Path(temp_path).unlink(missing_ok=True)
            return

        if error_msg[0]:
            play_sound("error")
            Path(temp_path).unlink(missing_ok=True)
            QMessageBox.critical(self, "Download Failed", str(error_msg[0]))
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
        from niruvi.desktop.desktop_utils import get_version

        version = get_version(dest_dir) or latest_version
        registry = InstallationRegistry()
        record = registry.get(self._app_name)
        if record:
            record.version = version
            registry.add(record)
        play_sound("success")
        QMessageBox.information(self, "Update Complete", f"{self._app_name} has been updated to version {version}.")
        self._status(f"Updated {self._app_name} to version {version}")

    def _run_app(self):
        parent = self.parent()
        if parent and hasattr(parent, "_run_app"):
            parent._run_app(self._app_name)

    def _open_install_dir(self):
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        app_dir = self._info.get("path", "")
        if not app_dir or not os.path.isdir(app_dir):
            play_sound("warning")
            QMessageBox.information(self, "Nothing to Open", f"The install folder does not exist:\n{app_dir}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(app_dir))

    def _on_policy_changed(self):
        record = self._get_or_create_record()
        self._update_header_chip(record)
        if getattr(self, "_security_page", None) is not None:
            self._security_page._on_policy_changed()

    def _update_header_chip(self, record=None):
        """Derive the isolation summary from the backend — never assumed."""
        if getattr(self, "_header_chip", None) is None:
            return
        if record is None:
            record = self._get_or_create_record()
        policy = resolve_policy(self._app_name, record)
        if not policy.enabled:
            self._header_chip.setText("Isolation off — runs with full user access")
            self._header_chip.setStyleSheet(style(f"color: {COLOR_ERROR}; font-size: 11px;"))
            return
        status = get_backend(policy.backend).get_enforcement_status(policy)
        color = {
            SANDBOXED: COLOR_SUCCESS,
            PARTIALLY_RESTRICTED: COLOR_WARNING,
            NOT_SANDBOXED: COLOR_ERROR,
        }.get(status.state, COLOR_WARNING)
        label = STATE_LABELS.get(status.state, status.state)
        text = f"{label} ({get_backend(policy.backend).display_name})"
        if status.detail:
            text += f" — {status.detail}"
        self._header_chip.setText(text)
        self._header_chip.setStyleSheet(style(f"color: {color}; font-size: 11px;"))

    def _on_storage_report(self, report):
        """Fill the Storage tab from the worker's StorageReport."""
        from niruvi.core.storage import ROLLBACK_SUFFIX

        if getattr(self, "_storage_rows", None) is None:
            return
        app_dir = self._info.get("path", "")
        while self._storage_rows.count():
            item = self._storage_rows.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._storage_report = report
        if report is None or not report.apps:
            self._storage_summary.setText("Storage not measurable — the install folder is missing.")
            self.btn_remove_prev.setEnabled(False)
            self.btn_cleanup_temp.setEnabled(False)
            return
        entry = report.apps[0]
        rows = [
            ("App files", entry.app_files),
            ("Update seed copy", entry.seed_images),
            ("Portable data (~/.home lookup)", entry.portable_home),
            ("Portable config", entry.portable_config),
            ("Rollback copy (.prev)", entry.rollback),
            ("Obsolete leftovers", entry.obsolete),
        ]
        any_row = False
        for label, value in rows:
            if not value:
                continue
            any_row = True
            row = QLabel(f"{label}: {format_size(value)}")
            row.setStyleSheet(style("font-size: 12px;"))
            self._storage_rows.addWidget(row)
        if not any_row:
            row = QLabel(f"Storage used: {format_size(entry.total)}")
            row.setStyleSheet(style("font-size: 12px;"))
            self._storage_rows.addWidget(row)
        if report.temp_dirs:
            row = QLabel(
                "Niruvi temporary files: " + format_size(report.temp_total) + " system-wide"
            )
            row.setStyleSheet(style("font-size: 12px;"))
            self._storage_rows.addWidget(row)
        self._storage_summary.setText(
            f"Total for {entry.name}: {format_size(entry.total)}"
            + (f" (+{format_size(report.temp_total)} temp)" if report.temp_total else "")
        )
        self.btn_remove_prev.setEnabled(os.path.isdir(app_dir + ROLLBACK_SUFFIX))
        self.btn_cleanup_temp.setEnabled(bool(report.temp_dirs))

    def _cleanup_temp(self):
        from niruvi.core.storage import cleanup_stale_temp_dirs

        play_sound("warning")
        reply = QMessageBox.question(
            self,
            "Clean Up Temporary Files",
            "Delete Niruvi temporary folders (staging copies, TMPDIR tests, crashed run leftovers)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            removed, reclaimed = cleanup_stale_temp_dirs()
            play_sound("success" if removed else "info")
            self._storage_summary.setText(
                f"Removed {removed} temp folder(s); reclaimed {format_size(reclaimed)}."
            )
            self._on_storage_report(self._storage_report)
        except Exception as e:
            play_sound("error")
            QMessageBox.critical(self, "Cleanup Failed", str(e))

    def _remove_rollback(self):
        from niruvi.core.storage import ROLLBACK_SUFFIX

        app_dir = self._info.get("path", "")
        prev_dir = app_dir + ROLLBACK_SUFFIX
        if not os.path.isdir(prev_dir):
            play_sound("info")
            QMessageBox.information(self, "Nothing to Delete", "No rollback copy exists for this app.")
            return
        play_sound("warning")
        reply = QMessageBox.question(
            self,
            "Delete Rollback Copy",
            f"Delete the rollback copy at {prev_dir}?\n\n"
            "After this the app cannot be reverted to the version before the last update.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            shutil.rmtree(prev_dir)
            play_sound("success")
            QMessageBox.information(self, "Rollback Copy Deleted", "Rollback copy removed successfully.")
        except Exception as e:
            play_sound("error")
            QMessageBox.critical(self, "Rollback Delete Failed", f"Could not delete rollback copy:\n{e}")
        finally:
            self.btn_remove_prev.setEnabled(os.path.isdir(prev_dir))

    def _uninstall_app(self):
        self.accept()
        parent = self.parent()
        if parent and hasattr(parent, "_uninstall_app"):
            parent._uninstall_app(self._app_name)

    def _reset_customization(self):
        play_sound("warning")
        reply = QMessageBox.question(
            self,
            "Reset Customization",
            f"Reset customization settings for {self._app_name} to defaults?\n\n"
            "This will clear custom display name, icon, run arguments, "
            "environment variables, and tags.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        record = self._get_or_create_record()
        record.display_name_override = ""
        record.run_args = ""
        record.env_vars = {}
        record.custom_icon_path = ""
        record.tags = []
        InstallationRegistry().add(record)
        self.display_name_edit.clear()
        self.run_args_edit.clear()
        self.env_table.setRowCount(0)
        self.tags_edit.clear()
        self._update_icon_preview()
        play_sound("success")
        self._status(f"Customization reset to defaults for {self._app_name}")

    def _reset_app_defaults(self):
        play_sound("warning")
        reply = QMessageBox.question(
            self,
            "Reset to Defaults",
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
        try:
            # Drop the per-app policy file so defaults apply again.
            PolicyStore().delete(self._app_name)
        except Exception as e:
            logger.debug("Could not delete policy file for %s: %s", self._app_name, e, exc_info=True)
        record.sandbox_config = {}
        InstallationRegistry().add(record)
        self.display_name_edit.clear()
        self.run_args_edit.clear()
        self.env_table.setRowCount(0)
        self.update_url_edit.clear()
        self.channel_combo.setCurrentIndex(0)
        self.auto_update_toggle.setChecked(False)
        self._update_icon_preview()
        self._update_header_chip(record)
        self._update_source_type_label()
        self._info["custom_icon_path"] = ""
        play_sound("success")
        self._status(f"All settings reset to defaults for {self._app_name}")

    def _update_files_status(self, app_dir: str):
        if getattr(self, "files_status", None) is None:
            return
        top = self.file_tree.topLevelItemCount()
        if top == 0:
            self.files_status.setText("This folder is empty.")
            return
        self.files_status.setText(
            f"{top} item(s) at the top level · calculating total size… "
            "Expand a folder to load its contents."
        )
        self._fs_worker = DirectoryStatsWorker(app_dir)
        self._fs_worker.done.connect(lambda count, total, n=top: self._on_files_stats(n, count, total))
        start_worker(self._fs_worker)

    def _on_files_stats(self, top: int, file_count: int, total: int):
        if getattr(self, "files_status", None) is None:
            return
        self.files_status.setText(
            f"{top} item(s) at the top level · {file_count} file(s) · {format_size(total)} on disk. "
            "Expand a folder to load its contents."
        )

    def _refresh_files(self):
        if getattr(self, "file_tree", None) is None:
            return
        self.file_tree.refresh()
        self._update_files_status(self._info.get("path", ""))

    def _status(self, msg: str):
        parent = self.parent()
        if parent and hasattr(parent, "_status_bar") and parent._status_bar is not None:
            parent._status_bar.showMessage(msg)
        else:
            # No status bar available (e.g. standalone dialog) — log instead
            logger.debug("Status: %s", msg)
