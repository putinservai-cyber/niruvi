"""Compact standalone permissions manager for AppImages."""

import logging
from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
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
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QStackedWidget,
)

from niruvi.core.backends import (
    PARTIAL,
    SUPPORTED,
    UNSUPPORTED,
    available_backends,
    get_backend,
)
from niruvi.core.policy import (
    DEVICE_KEYS,
    IPC_KEYS,
    PRESETS,
    FsGrant,
    dangerous_changes,
    describe_policy,
    preset_default,
    validate_path,
)
from niruvi.core.policy_store import PolicyStore, policy_mirror, resolve_policy
from niruvi.utils import get_icon, icon_pixmap
from niruvi.utils.sound_manager import play as play_sound
from niruvi.utils.styles import placeholder_style
from niruvi.utils.theme_engine import COLOR_ERROR, COLOR_SUCCESS, COLOR_WARNING

logger = logging.getLogger(__name__)

_MODE_LABELS = [("No access", "none"), ("Read-only", "ro"), ("Read & write", "rw")]

_CATEGORIES = [
    "Overview",
    "Filesystem",
    "Network",
    "Devices",
    "Display & IPC",
    "Audio & microphone",
    "Camera",
    "Environment",
    "Process & system",
    "Advanced",
    "History",
]

_LEVEL_COLORS = {SUPPORTED: COLOR_SUCCESS, PARTIAL: COLOR_WARNING, UNSUPPORTED: COLOR_ERROR}


def _level_text(backend_name: str, control: str) -> tuple[str, str]:
    cap = get_backend(backend_name).capability(control)
    label = {SUPPORTED: "Supported", PARTIAL: "Partially supported", UNSUPPORTED: "Not supported"}[cap.level]
    return cap.level, f"{label}: {cap.detail}" if cap.detail else label


class ChangePreviewDialog(QDialog):
    """Shows exactly what Apply will change before it happens."""

    def __init__(self, preview: dict, warnings: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Review permission changes")
        self.setMinimumSize(480, 380)
        layout = QVBoxLayout(self)

        if warnings:
            warn = QLabel("Risky changes:\n• " + "\n• ".join(warnings))
            warn.setWordWrap(True)
            warn.setStyleSheet(f"color: {COLOR_WARNING}; font-weight: bold;")
            layout.addWidget(warn)

        if preview.get("grants"):
            grant_lbl = QLabel("Will GRANT additional access.")
            grant_lbl.setStyleSheet(f"color: {COLOR_WARNING};")
            layout.addWidget(grant_lbl)
        if preview.get("revokes"):
            rev_lbl = QLabel("Will REVOKE access (app may stop working).")
            rev_lbl.setStyleSheet(f"color: {COLOR_ERROR};")
            layout.addWidget(rev_lbl)

        changes = preview.get("changes") or []
        if changes:
            text = QLabel("Changes:\n• " + "\n• ".join(changes))
        else:
            text = QLabel("No effective change.")
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(text)
        layout.addWidget(scroll, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        apply_btn = QPushButton("Apply changes")
        apply_btn.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(apply_btn)
        layout.addLayout(row)


class PermissionsWindow(QDialog):
    def __init__(self, apps, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Application Permissions")
        self.setMinimumSize(800, 550)
        self.resize(900, 600)
        self._apps = list(apps)
        self._app_by_id = {a["id"]: a for a in self._apps}
        self._store = PolicyStore()
        self._originals: dict[str, object] = {}
        self._previous: dict[str, object] = {}
        self._current_id: str | None = None
        self._loading = False
        self._gated: list[tuple[QWidget, str]] = []

        root = QHBoxLayout(self)
        root.setSpacing(6)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._build_sidebar(), 0)
        root.addWidget(self._build_main(), 1)

        self._populate_list()
        if self._apps:
            self._select_app(self._apps[0]["id"])

    def _build_sidebar(self) -> QWidget:
        panel = QWidget()
        panel.setFixedWidth(220)
        v = QVBoxLayout(panel)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search apps…")
        self.search.textChanged.connect(self._populate_list)
        v.addWidget(self.search)

        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All", "Sandboxed", "Not sandboxed", "Modified"])
        self.filter_combo.currentIndexChanged.connect(self._populate_list)
        v.addWidget(self.filter_combo)

        self.app_list = QListWidget()
        self.app_list.currentItemChanged.connect(self._on_list_selection)
        v.addWidget(self.app_list, 1)
        return panel

    def _status_for(self, app_id: str):
        record = self._record(app_id)
        policy = resolve_policy(app_id, record, self._store)
        return policy, get_backend(policy.backend, strict=True).get_enforcement_status(policy)

    def _populate_list(self):
        query = self.search.text().strip().lower()
        flt = self.filter_combo.currentText()
        self.app_list.blockSignals(True)
        self.app_list.clear()
        for app in self._apps:
            if query and query not in app["name"].lower():
                continue
            _policy, status = self._status_for(app["id"])
            modified = self._is_modified(app["id"])
            if flt == "Modified" and not modified:
                continue
            if flt == "Sandboxed" and status.state not in ("sandboxed", "partially_restricted"):
                continue
            if flt == "Not sandboxed" and status.state in ("sandboxed", "partially_restricted"):
                continue
            label = app["name"]
            if modified:
                label += "  *"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, app["id"])
            if app.get("icon_path"):
                item.setIcon(QIcon(app["icon_path"]))
            item.setToolTip(f"{app['name']}\n{app.get('path', '')}\nStatus: {status.label}")
            self.app_list.addItem(item)
        self.app_list.blockSignals(False)

    def _select_row(self, app_id: str):
        for i in range(self.app_list.count()):
            if self.app_list.item(i).data(Qt.ItemDataRole.UserRole) == app_id:
                self.app_list.setCurrentRow(i)
                return

    def _build_main(self) -> QWidget:
        panel = QWidget()
        v = QVBoxLayout(panel)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(6)
        self.header_icon = QLabel()
        self.header_icon.setFixedSize(28, 28)
        header.addWidget(self.header_icon)
        title_box = QVBoxLayout()
        title_box.setSpacing(6)
        self.header_name = QLabel()
        self.header_name.setStyleSheet("font-size: 14px; font-weight: bold;")
        self.header_path = QLabel()
        self.header_path.setStyleSheet(placeholder_style("", 10))
        self.header_path.setWordWrap(True)
        title_box.addWidget(self.header_name)
        title_box.addWidget(self.header_path)
        header.addLayout(title_box)
        header.addStretch(1)
        self.status_badge = QLabel()
        header.addWidget(self.status_badge)
        v.addLayout(header)

        self.stack = QStackedWidget()
        for name in _CATEGORIES:
            page_layout = QVBoxLayout()
            page_layout.setSpacing(6)
            page_widget = QWidget()
            page_widget.setLayout(page_layout)
            idx = self.stack.addWidget(page_widget)
            display_name = name.replace("&", "&&")
            setattr(self, f"_page_{name.lower().replace(' ', '_').replace('&', 'and').replace('/', '_')}", page_layout)
            # Store display name for reference if needed
        v.addWidget(self.stack, 1)

        self.status_line = QLabel("")
        self.status_line.setWordWrap(True)
        self.status_line.setStyleSheet(placeholder_style("", 10))
        v.addWidget(self.status_line)

        footer = QHBoxLayout()
        footer.setSpacing(6)
        self.btn_reset = QPushButton(get_icon("edit-undo"), "Reset")
        self.btn_reset.clicked.connect(self._reset_default)
        self.btn_discard = QPushButton(get_icon("edit-undo"), "Discard")
        self.btn_discard.clicked.connect(self._discard)
        self.btn_restore = QPushButton(get_icon("edit-undo"), "Restore")
        self.btn_restore.clicked.connect(self._restore_previous)
        self.btn_apply = QPushButton(get_icon("check"), "Apply")
        self.btn_apply.setProperty("variant", "primary")
        self.btn_apply.clicked.connect(self._apply)
        footer.addWidget(self.btn_reset)
        footer.addWidget(self.btn_discard)
        footer.addWidget(self.btn_restore)
        footer.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        footer.addWidget(close_btn)
        footer.addWidget(self.btn_apply)
        v.addLayout(footer)

        self._build_overview_tab()
        self._build_filesystem_tab()
        self._build_network_tab()
        self._build_devices_tab()
        self._build_ipc_tab()
        self._build_audio_tab()
        self._build_camera_tab()
        self._build_env_tab()
        self._build_process_tab()
        self._build_advanced_tab()
        self._build_history_tab()
        return panel

    def _wrap_scroll(self, layout: QVBoxLayout) -> QWidget:
        inner = QWidget()
        inner.setLayout(layout)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(inner)
        return scroll

    def _page(self, name: str) -> QVBoxLayout:
        key = name.lower().replace(" ", "_").replace("&", "and").replace("/", "_")
        return getattr(self, f"_page_{key}")

    def _register_control(self, widget: QWidget, control: str):
        self._gated.append((widget, control))

    def _build_overview_tab(self):
        layout = self._page("Overview")
        info = QFrame()
        info.setFrameShape(QFrame.Shape.StyledPanel)
        info.setLineWidth(1)
        form = QFormLayout(info)
        form.setSpacing(6)
        self.ov_name = QLabel()
        self.ov_path = QLabel()
        self.ov_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.ov_backend = QComboBox()
        self.ov_backend.currentIndexChanged.connect(self._on_backend_changed)
        form.addRow("Name", self.ov_name)
        form.addRow("Path", self.ov_path)
        form.addRow("Backend", self.ov_backend)
        layout.addWidget(info)

        self.ov_enabled = QCheckBox("Sandbox this app")
        self.ov_enabled.toggled.connect(self._mark_dirty)
        layout.addWidget(self.ov_enabled)

        self.ov_backend_note = QLabel()
        self.ov_backend_note.setWordWrap(True)
        self.ov_backend_note.setStyleSheet(placeholder_style("", 10))
        layout.addWidget(self.ov_backend_note)

        status_group = QFrame()
        status_group.setFrameShape(QFrame.Shape.StyledPanel)
        status_group.setLineWidth(1)
        sv = QVBoxLayout(status_group)
        sv = QVBoxLayout(status_group)
        sv.setSpacing(6)
        self.ov_status = QLabel()
        self.ov_status.setWordWrap(True)
        sv.addWidget(self.ov_status)
        self.ov_enforced = QLabel()
        self.ov_enforced.setWordWrap(True)
        self.ov_enforced.setStyleSheet(placeholder_style("", 10))
        sv.addWidget(self.ov_enforced)
        layout.addWidget(status_group)

        self.ov_summary = QLabel()
        self.ov_summary.setWordWrap(True)
        layout.addWidget(self.ov_summary)
        layout.addStretch(1)

    def _build_filesystem_tab(self):
        layout = self._page("Filesystem")
        note = QLabel("Filesystem restrictions enforced by sandbox backend. Changes take effect on next launch.")
        note.setWordWrap(True)
        note.setStyleSheet(placeholder_style("", 10))
        layout.addWidget(note)

        self.fs_support = QLabel()
        self.fs_support.setWordWrap(True)
        layout.addWidget(self.fs_support)

        home = QFrame()
        home.setFrameShape(QFrame.Shape.StyledPanel)
        home.setLineWidth(1)
        hf = QFormLayout(home)
        hf.setSpacing(6)
        self.fs_home = QComboBox()
        for label, value in _MODE_LABELS:
            self.fs_home.addItem(label, value)
        self.fs_home.currentIndexChanged.connect(self._mark_dirty)
        hf.addRow("Home folder access", self.fs_home)
        self._register_control(self.fs_home, "filesystem")
        layout.addWidget(home)

        dirs = QFrame()
        dirs.setFrameShape(QFrame.Shape.StyledPanel)
        dirs.setLineWidth(1)
        df = QFormLayout(dirs)
        df.setSpacing(6)
        self.fs_dirs = {}
        for key in ("downloads", "documents", "pictures", "videos", "music", "desktop"):
            combo = QComboBox()
            for label, value in _MODE_LABELS:
                combo.addItem(label, value)
            combo.currentIndexChanged.connect(self._mark_dirty)
            self.fs_dirs[key] = combo
            self._register_control(combo, "filesystem")
            df.addRow(key.capitalize(), combo)
        self.fs_removable = QComboBox()
        for label, value in _MODE_LABELS:
            self.fs_removable.addItem(label, value)
        self.fs_removable.currentIndexChanged.connect(self._mark_dirty)
        self._register_control(self.fs_removable, "filesystem")
        df.addRow("Removable storage", self.fs_removable)
        layout.addWidget(dirs)

        custom = QFrame()
        custom.setFrameShape(QFrame.Shape.StyledPanel)
        custom.setLineWidth(1)
        cv = QVBoxLayout(custom)
        cv.setSpacing(6)
        self.fs_paths = QTableWidget(0, 2)
        self.fs_paths.setHorizontalHeaderLabels(["Path", "Access"])
        self.fs_paths.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.fs_paths.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.fs_paths.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        cv.addWidget(self.fs_paths)
        btn_row = QHBoxLayout()
        add_btn = QPushButton(get_icon("list-add"), "Add")
        add_btn.clicked.connect(self._add_fs_path)
        rm_btn = QPushButton(get_icon("list-remove"), "Remove")
        rm_btn.clicked.connect(self._remove_fs_path)
        self._register_control(add_btn, "filesystem")
        self._register_control(rm_btn, "filesystem")
        btn_row.addWidget(add_btn)
        btn_row.addWidget(rm_btn)
        btn_row.addStretch(1)
        cv.addLayout(btn_row)
        hint = QLabel("Denied paths only effective outside granted folders.")
        hint.setWordWrap(True)
        hint.setStyleSheet(placeholder_style("", 10))
        cv.addWidget(hint)
        layout.addWidget(custom)
        layout.addStretch(1)

    def _build_network_tab(self):
        layout = self._page("Network")
        self.net_support = QLabel()
        self.net_support.setWordWrap(True)
        layout.addWidget(self.net_support)
        self.net_enabled = QCheckBox("Allow network access")
        self.net_enabled.toggled.connect(self._mark_dirty)
        self._register_control(self.net_enabled, "network")
        layout.addWidget(self.net_enabled)
        note = QLabel("Backend launches app without network. No host firewall changes.")
        note.setWordWrap(True)
        note.setStyleSheet(placeholder_style("", 10))
        layout.addWidget(note)
        layout.addStretch(1)

    def _build_devices_tab(self):
        layout = self._page("Devices")
        self.dev_support = QLabel()
        self.dev_support.setWordWrap(True)
        layout.addWidget(self.dev_support)
        self.dev_boxes = {}
        group = QFrame()
        group.setFrameShape(QFrame.Shape.StyledPanel)
        group.setLineWidth(1)
        gv = QVBoxLayout(group)
        gv.setSpacing(6)
        for key in DEVICE_KEYS:
            cb = QCheckBox(key.capitalize())
            cb.toggled.connect(self._mark_dirty)
            self.dev_boxes[key] = cb
            self._register_control(cb, "devices")
            gv.addWidget(cb)
        layout.addWidget(group)
        note = QLabel("Does not change host device permissions.")
        note.setWordWrap(True)
        note.setStyleSheet(placeholder_style("", 10))
        layout.addWidget(note)
        layout.addStretch(1)

    def _build_ipc_tab(self):
        layout = self._page("Display & IPC")
        self.ipc_support = QLabel()
        self.ipc_support.setWordWrap(True)
        layout.addWidget(self.ipc_support)
        self.ipc_boxes = {}
        group = QFrame()
        group.setFrameShape(QFrame.Shape.StyledPanel)
        group.setLineWidth(1)
        gv = QVBoxLayout(group)
        gv.setSpacing(6)
        labels = {
            "wayland": "Wayland socket",
            "x11": "X11 socket",
            "dbus_session": "Session D-Bus",
            "dbus_system": "System D-Bus",
            "ipc": "Shared memory IPC",
            "printing": "Printing",
            "ssh_agent": "SSH agent",
        }
        for key in IPC_KEYS:
            cb = QCheckBox(labels.get(key, key))
            cb.toggled.connect(self._mark_dirty)
            self.ipc_boxes[key] = cb
            self._register_control(cb, "ipc")
            gv.addWidget(cb)
        layout.addWidget(group)
        self.ipc_note = QLabel("Removing display access can prevent app from starting. Use Restore to undo.")
        self.ipc_note.setWordWrap(True)
        self.ipc_note.setStyleSheet(placeholder_style("", 10))
        layout.addWidget(self.ipc_note)
        layout.addStretch(1)

    def _build_audio_tab(self):
        layout = self._page("Audio & microphone")
        self.audio_support = QLabel()
        self.audio_support.setWordWrap(True)
        layout.addWidget(self.audio_support)
        self.audio_box = QCheckBox("Audio output")
        self.audio_box.toggled.connect(self._mark_dirty)
        self._register_control(self.audio_box, "devices")
        layout.addWidget(self.audio_box)
        self.mic_box = QCheckBox("Microphone")
        self.mic_box.toggled.connect(self._mark_dirty)
        self._register_control(self.mic_box, "devices")
        layout.addWidget(self.mic_box)
        layout.addStretch(1)

    def _build_camera_tab(self):
        layout = self._page("Camera")
        self.cam_support = QLabel()
        self.cam_support.setWordWrap(True)
        layout.addWidget(self.cam_support)
        self.cam_box = QCheckBox("Webcam / camera")
        self.cam_box.toggled.connect(self._mark_dirty)
        self._register_control(self.cam_box, "devices")
        layout.addWidget(self.cam_box)
        layout.addStretch(1)

    def _build_env_tab(self):
        layout = self._page("Environment")
        note = QLabel("Per-app environment variables passed directly to process (no shell).")
        note.setWordWrap(True)
        note.setStyleSheet(placeholder_style("", 10))
        layout.addWidget(note)
        self.env_support = QLabel()
        self.env_support.setWordWrap(True)
        layout.addWidget(self.env_support)
        self.env_table = QTableWidget(0, 2)
        self.env_table.setHorizontalHeaderLabels(["Variable", "Value"])
        self.env_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.env_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.env_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.env_table, 1)
        row = QHBoxLayout()
        add_btn = QPushButton(get_icon("list-add"), "Add")
        add_btn.clicked.connect(self._add_env_row)
        rm_btn = QPushButton(get_icon("list-remove"), "Remove")
        rm_btn.clicked.connect(self._remove_env_row)
        self._register_control(add_btn, "environment")
        self._register_control(rm_btn, "environment")
        row.addWidget(add_btn)
        row.addWidget(rm_btn)
        row.addStretch(1)
        layout.addLayout(row)

    def _build_process_tab(self):
        layout = self._page("Process & system")
        self.res_support = QLabel()
        self.res_support.setWordWrap(True)
        layout.addWidget(self.res_support)
        group = QFrame()
        group.setFrameShape(QFrame.Shape.StyledPanel)
        group.setLineWidth(1)
        gv = QVBoxLayout(group)
        gv.setSpacing(6)
        self.hardening_box = QCheckBox("Process hardening (rlimits, ptrace, malloc)")
        self.hardening_box.toggled.connect(self._mark_dirty)
        self.seccomp_box = QCheckBox("Syscall filtering (seccomp)")
        self.seccomp_box.toggled.connect(self._mark_dirty)
        self.landlock_box = QCheckBox("Filesystem enforcement (Landlock)")
        self.landlock_box.toggled.connect(self._mark_dirty)
        self.namespace_box = QCheckBox("User namespaces (needed to block network)")
        self.namespace_box.toggled.connect(self._mark_dirty)
        for w in (self.hardening_box, self.seccomp_box, self.landlock_box):
            self._register_control(w, "resources")
        self._register_control(self.namespace_box, "network")
        gv.addWidget(self.hardening_box)
        gv.addWidget(self.seccomp_box)
        gv.addWidget(self.landlock_box)
        gv.addWidget(self.namespace_box)
        layout.addWidget(group)

        limits = QFrame()
        limits.setFrameShape(QFrame.Shape.StyledPanel)
        limits.setLineWidth(1)
        lf = QFormLayout(limits)
        lf.setSpacing(6)
        self.mem_edit = QLineEdit()
        self.mem_edit.setPlaceholderText("e.g. 2G (blank = unlimited)")
        self.mem_edit.textChanged.connect(self._mark_dirty)
        self.cpu_edit = QLineEdit()
        self.cpu_edit.setPlaceholderText("CPU seconds (blank = unlimited)")
        self.cpu_edit.textChanged.connect(self._mark_dirty)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(0, 24 * 3600)
        self.timeout_spin.setSuffix(" s")
        self.timeout_spin.setSpecialValueText("No timeout")
        self.timeout_spin.valueChanged.connect(self._mark_dirty)
        lf.addRow("Memory limit", self.mem_edit)
        lf.addRow("CPU limit", self.cpu_edit)
        lf.addRow("Auto-stop after", self.timeout_spin)
        for w in (self.mem_edit, self.cpu_edit, self.timeout_spin):
            self._register_control(w, "resources")
        layout.addWidget(limits)
        layout.addStretch(1)

    def _build_advanced_tab(self):
        layout = self._page("Advanced")
        group = QFrame()
        group.setFrameShape(QFrame.Shape.StyledPanel)
        group.setLineWidth(1)
        gv = QVBoxLayout(group)
        gv.setSpacing(6)
        self.portable_home_box = QCheckBox("Portable .home folder")
        self.portable_config_box = QCheckBox("Portable .config folder")
        self.private_tmp_box = QCheckBox("Private /tmp for this app")
        for w in (self.portable_home_box, self.portable_config_box, self.private_tmp_box):
            w.toggled.connect(self._mark_dirty)
        gv.addWidget(self.portable_home_box)
        gv.addWidget(self.portable_config_box)
        gv.addWidget(self.private_tmp_box)
        layout.addWidget(group)

        preset_group = QFrame()
        preset_group.setFrameShape(QFrame.Shape.StyledPanel)
        preset_group.setLineWidth(1)
        pv = QVBoxLayout(preset_group)
        pv.setSpacing(6)
        prow = QHBoxLayout()
        self.preset_combo = QComboBox()
        for key, (label, _fn) in PRESETS.items():
            self.preset_combo.addItem(label, key)
        apply_preset = QPushButton("Use preset")
        apply_preset.clicked.connect(self._apply_preset)
        prow.addWidget(self.preset_combo)
        prow.addWidget(apply_preset)
        pv.addLayout(prow)
        pv.addWidget(QLabel("Presets previewed before applying."))
        layout.addWidget(preset_group)

        cap_group = QFrame()
        cap_group.setFrameShape(QFrame.Shape.StyledPanel)
        cap_group.setLineWidth(1)
        cv = QVBoxLayout(cap_group)
        cv.setSpacing(6)
        self.cap_label = QLabel()
        self.cap_label.setWordWrap(True)
        cv.addWidget(self.cap_label)
        layout.addWidget(cap_group)

        self.raw_label = QLabel()
        self.raw_label.setWordWrap(True)
        self.raw_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.raw_label.setStyleSheet("font-family: monospace; font-size: 10px; color: palette(placeholder-text);")
        layout.addWidget(self.raw_label)
        layout.addStretch(1)

    def _build_history_tab(self):
        layout = self._page("History")
        note = QLabel("Recent permission changes for this app.")
        note.setWordWrap(True)
        note.setStyleSheet(placeholder_style("", 10))
        layout.addWidget(note)
        self.history_table = QTableWidget(0, 3)
        self.history_table.setHorizontalHeaderLabels(["When", "Action", "Changes"])
        self.history_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.history_table, 1)
        row = QHBoxLayout()
        export_btn = QPushButton(get_icon("document-save-as"), "Export")
        export_btn.clicked.connect(self._export_policy)
        import_btn = QPushButton(get_icon("document-open"), "Import")
        import_btn.clicked.connect(self._import_policy)
        row.addWidget(export_btn)
        row.addWidget(import_btn)
        row.addStretch(1)
        layout.addLayout(row)

    def _on_list_selection(self, current, _previous):
        if current is None:
            return
        app_id = current.data(Qt.ItemDataRole.UserRole)
        self._select_app(app_id)

    def _select_app(self, app_id: str):
        self._current_id = app_id
        app = self._app_by_id.get(app_id)
        if not app:
            return
        record = self._record(app_id)
        if app_id not in self._originals:
            self._originals[app_id] = resolve_policy(app_id, record, self._store)
        self.header_name.setText(app["name"])
        self.header_path.setText(app.get("path", ""))
        self.header_icon.clear()
        if app.get("icon_path"):
            self.header_icon.setPixmap(icon_pixmap(QIcon(app["icon_path"]), 28))
        self._loading = True
        self._load_backend_combo(self._originals[app_id].backend)
        self._populate_from_policy(self._originals[app_id])
        self._loading = False
        self._apply_gating()
        self._refresh_status()
        self._refresh_history()

    def _load_backend_combo(self, selected: str):
        from niruvi.core.backends import BubblewrapBackend, FirejailBackend, ShieldBackend

        self.ov_backend.blockSignals(True)
        self.ov_backend.clear()
        available = available_backends()
        for cls in (ShieldBackend, FirejailBackend, BubblewrapBackend):
            inst = available.get(cls.name) or cls()
            label = inst.display_name + ("" if cls.name in available else "  (unavailable)")
            self.ov_backend.addItem(label, cls.name)
        for i in range(self.ov_backend.count()):
            if self.ov_backend.itemData(i) == selected:
                self.ov_backend.setCurrentIndex(i)
                break
        self.ov_backend.blockSignals(False)

    def _populate_from_policy(self, policy):
        self.ov_name.setText(policy.app_id or "")
        self.ov_enabled.setChecked(policy.enabled)
        self._set_combo(self.fs_home, policy.home_access)
        for key, combo in self.fs_dirs.items():
            self._set_combo(combo, policy.dirs.get(key, "none"))
        self._set_combo(self.fs_removable, policy.removable)
        self._populate_fs_paths(policy.custom_paths)
        self.net_enabled.setChecked(policy.network)
        for key, cb in self.dev_boxes.items():
            cb.setChecked(bool(policy.devices.get(key, False)))
        for key, cb in self.ipc_boxes.items():
            cb.setChecked(bool(policy.ipc.get(key, True)))
        self.audio_box.setChecked(bool(policy.devices.get("audio", True)))
        self.mic_box.setChecked(bool(policy.devices.get("microphone", False)))
        self.cam_box.setChecked(bool(policy.devices.get("camera", False)))
        self._populate_env(policy.env)
        self.hardening_box.setChecked(policy.hardening)
        self.seccomp_box.setChecked(policy.seccomp)
        self.landlock_box.setChecked(policy.landlock)
        self.namespace_box.setChecked(policy.use_namespace)
        self.mem_edit.setText(policy.memory_limit)
        self.cpu_edit.setText(policy.cpu_limit)
        self.timeout_spin.setValue(int(policy.timeout or 0))
        self.portable_home_box.setChecked(policy.portable_home)
        self.portable_config_box.setChecked(policy.portable_config)
        self.private_tmp_box.setChecked(policy.private_tmp)

    @staticmethod
    def _set_combo(combo: QComboBox, value: str):
        idx = combo.findData(value)
        combo.setCurrentIndex(idx if idx >= 0 else 0)

    def _populate_fs_paths(self, grants):
        self.fs_paths.setRowCount(len(grants))
        for i, grant in enumerate(grants):
            self.fs_paths.setItem(i, 0, QTableWidgetItem(grant.path))
            combo = QComboBox()
            for label, value in [*_MODE_LABELS, ("Denied", "deny")]:
                combo.addItem(label, value)
            self._set_combo(combo, grant.mode)
            combo.currentIndexChanged.connect(self._mark_dirty)
            self.fs_paths.setCellWidget(i, 1, combo)

    def _populate_env(self, env):
        self.env_table.setRowCount(len(env))
        for i, (key, value) in enumerate(sorted(env.items())):
            self.env_table.setItem(i, 0, QTableWidgetItem(key))
            self.env_table.setItem(i, 1, QTableWidgetItem(value))

    def _refresh_history(self):
        app_id = self._current_id
        self.history_table.setRowCount(0)
        if not app_id:
            return
        entries = list(reversed(self._store.history(app_id)))
        self.history_table.setRowCount(len(entries))
        for i, entry in enumerate(entries):
            when = datetime.fromtimestamp(entry.get("timestamp", 0)).strftime("%Y-%m-%d %H:%M:%S")
            self.history_table.setItem(i, 0, QTableWidgetItem(when))
            self.history_table.setItem(i, 1, QTableWidgetItem(entry.get("note", "")))
            changes = entry.get("changes") or []
            self.history_table.setItem(i, 2, QTableWidgetItem("; ".join(changes) if changes else "—"))

    def _collect_policy(self):
        base = self._originals[self._current_id].copy()
        base.app_id = self._current_id
        base.backend = self.ov_backend.currentData()
        base.enabled = self.ov_enabled.isChecked()
        base.home_access = self.fs_home.currentData()
        base.dirs = {key: combo.currentData() for key, combo in self.fs_dirs.items()}
        base.removable = self.fs_removable.currentData()
        grants = []
        for i in range(self.fs_paths.rowCount()):
            path_item = self.fs_paths.item(i, 0)
            combo = self.fs_paths.cellWidget(i, 1)
            if path_item and path_item.text().strip():
                grants.append(FsGrant(path=path_item.text().strip(), mode=combo.currentData()))
        base.custom_paths = grants
        base.network = self.net_enabled.isChecked()
        devices = dict(base.devices)
        for key, cb in self.dev_boxes.items():
            devices[key] = cb.isChecked()
        devices["audio"] = self.audio_box.isChecked()
        devices["microphone"] = self.mic_box.isChecked()
        devices["camera"] = self.cam_box.isChecked()
        base.devices = devices
        base.ipc = {key: cb.isChecked() for key, cb in self.ipc_boxes.items()}
        base.env = self._collect_env()
        base.hardening = self.hardening_box.isChecked()
        base.seccomp = self.seccomp_box.isChecked()
        base.landlock = self.landlock_box.isChecked()
        base.use_namespace = self.namespace_box.isChecked()
        base.memory_limit = self.mem_edit.text().strip()
        base.cpu_limit = self.cpu_edit.text().strip()
        base.timeout = self.timeout_spin.value()
        base.portable_home = self.portable_home_box.isChecked()
        base.portable_config = self.portable_config_box.isChecked()
        base.private_tmp = self.private_tmp_box.isChecked()
        return base

    def _collect_env(self) -> dict:
        env = {}
        for i in range(self.env_table.rowCount()):
            key_item = self.env_table.item(i, 0)
            value_item = self.env_table.item(i, 1)
            if key_item and key_item.text().strip():
                env[key_item.text().strip()] = value_item.text() if value_item else ""
        return env

    def _mark_dirty(self, *_):
        if self._loading or not self._current_id:
            return
        self._apply_gating()
        self._refresh_status(pending=True)
        self._refresh_list_row()

    def _is_modified(self, app_id: str) -> bool:
        if app_id not in self._originals:
            return False
        if self._current_id == app_id:
            try:
                return self._collect_policy().to_dict() != self._originals[app_id].to_dict()
            except Exception:
                return False
        return False

    def _refresh_list_row(self):
        app_id = self._current_id
        for i in range(self.app_list.count()):
            item = self.app_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == app_id:
                name = self._app_by_id[app_id]["name"]
                item.setText(name + ("  *" if self._is_modified(app_id) else ""))
                break

    def _apply_gating(self):
        if self._current_id:
            backend_name = self.ov_backend.currentData() or "shield"
        else:
            backend_name = "shield"
        backend = get_backend(backend_name, strict=True)
        for widget, control in self._gated:
            cap = backend.capability(control)
            widget.setEnabled(cap.level != UNSUPPORTED)
            widget.setToolTip(cap.detail)
        for attr, control in (
            ("fs_support", "filesystem"),
            ("net_support", "network"),
            ("dev_support", "devices"),
            ("ipc_support", "ipc"),
            ("audio_support", "devices"),
            ("cam_support", "devices"),
            ("env_support", "environment"),
            ("res_support", "resources"),
        ):
            label = getattr(self, attr, None)
            if label is None:
                continue
            level, text = _level_text(backend_name, control)
            label.setText(text)
            label.setStyleSheet(f"color: {_LEVEL_COLORS[level]}; font-size: 10px;")
        if hasattr(self, "cap_label"):
            caps = backend.capabilities()
            self.cap_label.setText(
                "\n".join(f"{k.capitalize()}: {v.level} — {v.detail}" for k, v in caps.items())
            )

    def _refresh_status(self, pending: bool = False):
        if not self._current_id:
            return
        policy = self._collect_policy()
        backend = get_backend(policy.backend, strict=True)
        status = backend.get_enforcement_status(policy, pending=pending)
        color = {
            "sandboxed": COLOR_SUCCESS,
            "partially_restricted": COLOR_WARNING,
            "not_sandboxed": COLOR_ERROR,
            "configuration_error": COLOR_ERROR,
            "backend_unavailable": COLOR_ERROR,
        }.get(status.state, COLOR_WARNING)
        self.status_badge.setText(status.label)
        self.status_badge.setStyleSheet(f"color: {color}; font-weight: bold;")
        self.ov_status.setText(f"<b>{status.label}</b><br>{status.detail}")
        self.ov_status.setStyleSheet(f"color: {color};")
        parts = []
        if status.enforced:
            parts.append("Enforced: " + ", ".join(status.enforced))
        if status.not_enforced:
            parts.append("Not enforced: " + ", ".join(status.not_enforced))
        self.ov_enforced.setText("\n".join(parts))
        self.ov_summary.setText("\n".join(describe_policy(policy)))
        self.raw_label.setText("<pre>" + policy_mirror(policy).__repr__() + "</pre>")
        self.btn_restore.setEnabled(self._current_id in self._previous)
        self.btn_discard.setEnabled(self._is_modified(self._current_id))

    def _on_backend_changed(self, *_):
        if self._loading:
            return
        self._apply_gating()
        self._refresh_status(pending=True)

    def _add_fs_path(self):
        directory = QFileDialog.getExistingDirectory(self, "Add folder")
        if not directory:
            return
        try:
            normalized = validate_path(directory)
        except Exception as e:
            QMessageBox.warning(self, "Invalid path", str(e))
            return
        row = self.fs_paths.rowCount()
        self.fs_paths.insertRow(row)
        self.fs_paths.setItem(row, 0, QTableWidgetItem(normalized))
        combo = QComboBox()
        for label, value in [*_MODE_LABELS, ("Denied", "deny")]:
            combo.addItem(label, value)
        combo.currentIndexChanged.connect(self._mark_dirty)
        self.fs_paths.setCellWidget(row, 1, combo)
        self._mark_dirty()

    def _remove_fs_path(self):
        rows = sorted({i.row() for i in self.fs_paths.selectedItems()}, reverse=True)
        for row in rows:
            self.fs_paths.removeRow(row)
        if rows:
            self._mark_dirty()

    def _add_env_row(self):
        row = self.env_table.rowCount()
        self.env_table.insertRow(row)
        self.env_table.setItem(row, 0, QTableWidgetItem(""))
        self.env_table.setItem(row, 1, QTableWidgetItem(""))
        self.env_table.editItem(self.env_table.item(row, 0))
        self._mark_dirty()

    def _remove_env_row(self):
        rows = sorted({i.row() for i in self.env_table.selectedItems()}, reverse=True)
        for row in rows:
            self.env_table.removeRow(row)
        if rows:
            self._mark_dirty()

    def _record(self, app_id: str):
        try:
            from niruvi.desktop.installation_registry import InstallationRegistry
            return InstallationRegistry().get(app_id)
        except Exception:
            return None

    def _reset_default(self):
        if not self._current_id:
            return
        policy = preset_default(self._current_id, self.ov_backend.currentData() or "shield")
        policy.portable_home = self.portable_home_box.isChecked()
        policy.portable_config = self.portable_config_box.isChecked()
        self._loading = True
        self._populate_from_policy(policy)
        self._loading = False
        self._apply_gating()
        self._refresh_status(pending=True)
        self._refresh_list_row()
        play_sound("click")

    def _apply_preset(self):
        if not self._current_id:
            return
        key = self.preset_combo.currentData()
        fn = PRESETS[key][1]
        proposed = fn(self._current_id, self.ov_backend.currentData() or "shield")
        current = self._collect_policy()
        backend = get_backend(proposed.backend, strict=True)
        preview = backend.preview_changes(current, proposed)
        warnings = dangerous_changes(proposed)
        if preview.get("grants"):
            warnings.append("Preset grants more access than current config.")
        if not ChangePreviewDialog(preview, warnings, self).exec():
            return
        self._loading = True
        self._populate_from_policy(proposed)
        self._loading = False
        self._apply_gating()
        self._refresh_status(pending=True)
        self._refresh_list_row()

    def _discard(self):
        if not self._current_id:
            return
        original = self._originals[self._current_id]
        self._loading = True
        self._load_backend_combo(original.backend)
        self._populate_from_policy(original)
        self._loading = False
        self._apply_gating()
        self._refresh_status()
        self._refresh_list_row()
        play_sound("navigation")

    def _apply(self):
        app_id = self._current_id
        if not app_id:
            return
        policy = self._collect_policy()
        backend = get_backend(policy.backend, strict=True)
        issues = backend.validate_policy(policy)
        if issues:
            play_sound("error")
            QMessageBox.warning(self, "Invalid configuration", "Please fix:\n• " + "\n• ".join(issues))
            return
        try:
            policy = policy.normalized()
        except Exception as e:
            play_sound("error")
            QMessageBox.warning(self, "Invalid configuration", str(e))
            return
        original = self._originals[app_id]
        preview = backend.preview_changes(original, policy)
        warnings = dangerous_changes(policy)
        if not ChangePreviewDialog(preview, warnings, self).exec():
            return
        record = self._record(app_id)
        self._previous[app_id] = original.copy()
        result = backend.apply_policy(app_id, policy, record, self._store)
        if not result.ok:
            backend.restore_previous_policy(app_id, original, record, self._store)
            play_sound("error")
            QMessageBox.critical(self, "Could not apply", f"{result.error}\n\nPrevious config restored.")
            return
        self._originals[app_id] = policy
        self._loading = True
        self._populate_from_policy(policy)
        self._loading = False
        self._apply_gating()
        self._refresh_status()
        self._refresh_list_row()
        self._refresh_history()
        play_sound("success")
        status = backend.get_enforcement_status(policy)
        message = "Permissions saved. " + status.label + "." if result.ok else result.error
        if result.warnings:
            message += " " + "; ".join(result.warnings)
        self.status_line.setText(message)

    def _restore_previous(self):
        app_id = self._current_id
        if not app_id or app_id not in self._previous:
            return
        previous = self._previous[app_id]
        backend = get_backend(previous.backend, strict=True)
        record = self._record(app_id)
        result = backend.apply_policy(app_id, previous, record, self._store)
        if not result.ok:
            QMessageBox.critical(self, "Restore failed", result.error)
            return
        self._originals[app_id] = previous.copy()
        self._loading = True
        self._load_backend_combo(previous.backend)
        self._populate_from_policy(previous)
        self._loading = False
        self._apply_gating()
        self._refresh_status()
        self._refresh_list_row()
        self._refresh_history()
        self.status_line.setText("Restored previous configuration.")
        play_sound("success")

    def _export_policy(self):
        if not self._current_id:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export policy", f"{self._current_id}.niruvi-policy.json")
        if not path:
            return
        if self._store.export_to_file(self._current_id, path):
            self.status_line.setText(f"Exported policy to {path}")
        else:
            QMessageBox.warning(self, "Export failed", "Could not write the policy file.")

    def _import_policy(self):
        if not self._current_id:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import policy", "", "Policy files (*.json)")
        if not path:
            return
        reply = QMessageBox.question(
            self, "Import policy",
            "Replace current permissions with imported policy? Previewed before saving.",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            import json
            with open(path) as f:
                data = json.load(f)
            from niruvi.core.policy import SandboxPolicy
            policy = SandboxPolicy.from_dict(data.get("policy", {}))
        except Exception as e:
            QMessageBox.warning(self, "Import failed", str(e))
            return
        policy.app_id = self._current_id
        self._loading = True
        self._populate_from_policy(policy)
        self._loading = False
        self._apply_gating()
        self._refresh_status(pending=True)
        self._refresh_list_row()
        self.status_line.setText("Imported policy loaded — review and click Apply.")