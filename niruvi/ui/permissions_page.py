"""Android-style per-application permissions panel (compact version).

Embedded in App Info dialog. Controls are bound to SandboxPolicy fields.
"""

import logging
from copy import deepcopy

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from niruvi.core.backends import (
    SUPPORTED,
    UNSUPPORTED,
    available_backends,
    get_backend,
)
from niruvi.core.policy import (
    DEVICE_KEYS,
    IPC_KEYS,
    SandboxPolicy,
    dangerous_changes,
    describe_policy,
    diff_policies,
    preset_default,
)
from niruvi.core.policy_store import PolicyStore, resolve_policy
from niruvi.desktop.installation_registry import InstallationRegistry
from niruvi.utils import get_icon, icon_pixmap
from niruvi.utils.sound_manager import play as play_sound
from niruvi.utils.styles import placeholder_style
from niruvi.utils.theme_engine import COLOR_ERROR, COLOR_SUCCESS, COLOR_WARNING
from niruvi.ui.toggle_switch import ToggleSwitch

logger = logging.getLogger(__name__)

_MODE_LABELS = [("No access", "none"), ("Read-only", "ro"), ("Read & write", "rw")]

_LEVEL_COLOR = {SUPPORTED: COLOR_SUCCESS, UNSUPPORTED: COLOR_ERROR}
_LEVEL_WORD = {SUPPORTED: "Supported", UNSUPPORTED: "Not supported"}

_DIR_META = {
    "downloads": ("Downloads", "folder-new"),
    "documents": ("Documents", "folder-documents"),
    "pictures": ("Pictures", "folder-pictures"),
    "videos": ("Videos", "folder-videos"),
    "music": ("Music", "folder-music"),
    "desktop": ("Desktop", "user-desktop"),
}

_DEVICE_META = {
    "gpu": ("Graphics", "video-display", "GPU rendering and video decode"),
    "audio": ("Audio out", "audio-x-generic", "Play sounds"),
    "microphone": ("Microphone", "microphone", "Record audio"),
    "camera": ("Camera", "camera-photo", "Capture from webcam"),
    "usb": ("USB", "camera-usb", "Generic USB device access"),
    "input": ("Input", "input-keyboard", "Extra input devices"),
}

_IPC_META = {
    "wayland": ("Wayland", "video-display", "Open windows on Wayland"),
    "x11": ("X11", "video-display", "Open windows on X11"),
    "dbus_session": ("Session D-Bus", "network-wired", "Notifications, tray, portals"),
    "dbus_system": ("System D-Bus", "network-server", "NetworkManager, udisks"),
    "ipc": ("Shared memory", "view-sort-descending", "POSIX message queues"),
    "printing": ("Printing", "document-print", "CUPS printing"),
    "ssh_agent": ("SSH agent", "password-show-on", "SSH keys for git"),
}


class _Entry(QFrame):
    """Compact permission row: icon, title/description, state, control."""

    def __init__(self, icon_name: str, title: str, description: str, control: QWidget):
        super().__init__()
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.control = control
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        ico = QLabel()
        pix = icon_pixmap(get_icon(icon_name, "help-about"), 18)
        if pix is not None and not pix.isNull():
            ico.setPixmap(pix)
        ico.setFixedSize(22, 22)
        ico.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(ico)

        text_col = QVBoxLayout()
        text_col.setSpacing(6)
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("font-weight: bold; font-size: 12px;")
        text_col.addWidget(title_lbl)
        if description:
            desc_lbl = QLabel(description)
            desc_lbl.setWordWrap(True)
            desc_lbl.setStyleSheet(placeholder_style("", 10))
            text_col.addWidget(desc_lbl)
        lay.addLayout(text_col, 1)

        self.state_label = QLabel("Supported")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        self.state_label.setStyleSheet(placeholder_style("", 10))
        self.state_label.setMinimumWidth(100)
        lay.addWidget(self.state_label)

        lay.addWidget(control)

    def set_gating(self, enabled: bool, state_text: str, color: str | None) -> None:
        self.state_label.setText(state_text)
        if color:
            self.state_label.setStyleSheet(f"color: {color}; font-size: 10px; font-weight: 500;")
        else:
            self.state_label.setStyleSheet(placeholder_style("", 10))
        self.setEnabled(enabled)


class PermissionsPage(QWidget):
    """Compact per-application permissions panel for App Info dialog."""

    policy_changed = pyqtSignal()

    def __init__(self, app_name: str, app_info: dict, parent=None):
        super().__init__(parent)
        self._app_name = app_name
        self._info = app_info
        self._record = InstallationRegistry().get(app_name)
        self._policy = self._load_policy()
        self._original = deepcopy(self._policy)
        self._entries: list[_Entry] = []
        self._dir_entries: dict[str, _Entry] = {}
        self._device_entries: dict[str, _Entry] = {}
        self._ipc_entries: dict[str, _Entry] = {}
        self._verification_cache: dict | None = None

        self._build_ui()
        self._load_widget_state()
        self._refresh_gating()

    def _load_policy(self) -> SandboxPolicy:
        store = PolicyStore()
        policy = store.load_if_valid(self._app_name)
        if policy is not None:
            return policy
        return resolve_policy(self._app_name, self._record)

    def _backend(self):
        name = self._backend_combo.currentData() if hasattr(self, "_backend_combo") else self._policy.backend
        return get_backend(name, strict=False)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll, 1)
        content = QWidget()
        scroll.setWidget(content)
        col = QVBoxLayout(content)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)

        # Master toggle + backend
        master_frame = QFrame()
        master_frame.setFrameShape(QFrame.Shape.StyledPanel)
        mcol = QVBoxLayout(master_frame)
        mcol.setContentsMargins(0, 0, 0, 0)
        mcol.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(6)
        ico = QLabel()
        ico.setPixmap(icon_pixmap(get_icon("security-high"), 18))
        row.addWidget(ico)
        label = QLabel("Isolate this app")
        label.setStyleSheet("font-weight: bold; font-size: 13px;")
        row.addWidget(label)
        row.addStretch()
        row.addWidget(QLabel("Backend:"))
        self._backend_combo = QComboBox()
        self._backend_combo.setMinimumWidth(160)
        for name in ("shield", "firejail", "bwrap"):
            backend = get_backend(name, strict=False)
            if backend.available():
                self._backend_combo.addItem(backend.display_name, name)
        self._backend_combo.currentIndexChanged.connect(self._on_backend_selected)
        row.addWidget(self._backend_combo)
        self._master_toggle = ToggleSwitch(self)
        self._master_toggle.toggled.connect(self._on_master_toggled)
        row.addWidget(self._master_toggle)
        mcol.addLayout(row)

        self._enforcement_label = QLabel()
        self._enforcement_label.setWordWrap(True)
        self._enforcement_label.setStyleSheet(placeholder_style("", 10))
        mcol.addWidget(self._enforcement_label)

        verify_row = QHBoxLayout()
        self._verify_btn = QPushButton(get_icon("view-refresh"), "Verify")
        self._verify_btn.setToolTip("Run real probes to verify enforcement")
        self._verify_btn.clicked.connect(self._run_verification)
        verify_row.addWidget(self._verify_btn)
        verify_row.addStretch()
        mcol.addLayout(verify_row)

        col.addWidget(master_frame)

        # Filesystem
        self._fs = self._category("Filesystem", "folder-open")
        self._cat_filesystem(self._fs)
        col.addWidget(self._fs["frame"])

        # Network
        self._net = self._category("Network", "network-wired")
        self._cat_network(self._net)
        col.addWidget(self._net["frame"])

        # Devices
        self._dev = self._category("Devices", "camera-usb")
        self._cat_devices(self._dev)
        col.addWidget(self._dev["frame"])

        # System (IPC + hardening)
        self._sys = self._category("System", "computer")
        self._cat_system(self._sys)
        col.addWidget(self._sys["frame"])

        # Advanced details (collapsed)
        adv_row = QHBoxLayout()
        self._adv_btn = QPushButton(get_icon("dialog-info"), "Advanced details…")
        self._adv_btn.setCheckable(True)
        self._adv_btn.setFlat(True)
        self._adv_btn.clicked.connect(self._toggle_advanced)
        adv_row.addWidget(self._adv_btn)
        adv_row.addStretch()
        col.addLayout(adv_row)
        self._adv_details = QLabel()
        self._adv_details.setWordWrap(True)
        self._adv_details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._adv_details.setStyleSheet(placeholder_style("", 10))
        self._adv_details.setVisible(False)
        col.addWidget(self._adv_details)

        col.addStretch()

        outer.addWidget(self._build_bar())

    def _category(self, title: str, icon_name: str) -> dict:
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        flow = QVBoxLayout(frame)
        flow.setContentsMargins(0, 0, 0, 0)
        flow.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(6)
        head_ic = QLabel()
        pix = icon_pixmap(get_icon(icon_name, "folder"), 14)
        if pix is not None and not pix.isNull():
            head_ic.setPixmap(pix)
        head.addWidget(head_ic)
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("font-weight: bold; font-size: 11px;")
        head.addWidget(title_lbl)
        head.addStretch()
        flow.addLayout(head)
        return {"frame": frame, "layout": flow}

    def _add_entry(self, cat: dict, icon: str, title: str, desc: str, control: QWidget) -> _Entry:
        entry = _Entry(icon, title, desc, control)
        cat["layout"].addWidget(entry)
        self._entries.append(entry)
        return entry

    def _mode_combo(self) -> QComboBox:
        combo = QComboBox()
        for label, _mode in _MODE_LABELS:
            combo.addItem(label)
        combo.setMinimumHeight(24)
        return combo

    def _cat_filesystem(self, cat: dict):
        self._home_combo = combo = self._mode_combo()
        combo.currentIndexChanged.connect(self._on_field_changed)
        self._home_entry = self._add_entry(
            cat, "go-home", "Home directory",
            "How much of your home folder the app can see and change.",
            combo,
        )
        for key in _DIR_META:
            title, icon = _DIR_META[key]
            dir_combo = self._mode_combo()
            dir_combo.currentIndexChanged.connect(self._on_field_changed)
            setattr(self, f"_dir_combo_{key}", dir_combo)
            self._dir_entries[key] = self._add_entry(
                cat, icon, f"{title} folder", "Grants access to this folder.",
                dir_combo,
            )
        self._removable_combo = self._mode_combo()
        self._removable_combo.currentIndexChanged.connect(self._on_field_changed)
        self._removable_entry = self._add_entry(
            cat, "media-removable", "Removable storage",
            "USB sticks, SD cards and other mounted removable media.",
            self._removable_combo,
        )
        info = QLabel("The app directory (and any portable .home/.config) is always accessible.")
        info.setWordWrap(True)
        info.setStyleSheet(placeholder_style("", 10))
        cat["layout"].addWidget(info)

    def _cat_network(self, cat: dict):
        self._network_toggle = ToggleSwitch(self)
        self._network_toggle.toggled.connect(self._on_field_changed)
        self._network_entry = self._add_entry(
            cat, "network-wired", "Network access",
            "Internet and local-network reachability.",
            self._network_toggle,
        )

    def _cat_devices(self, cat: dict):
        self._device_toggles: dict[str, ToggleSwitch] = {}
        for key in DEVICE_KEYS:
            title, icon, desc = _DEVICE_META[key]
            sw = ToggleSwitch(self)
            sw.toggled.connect(self._on_field_changed)
            self._device_toggles[key] = sw
            self._device_entries[key] = self._add_entry(cat, icon, title, desc, sw)

    def _cat_system(self, cat: dict):
        self._ipc_toggles: dict[str, ToggleSwitch] = {}
        for key in IPC_KEYS:
            title, icon, desc = _IPC_META[key]
            sw = ToggleSwitch(self)
            sw.toggled.connect(self._on_field_changed)
            self._ipc_toggles[key] = sw
            self._ipc_entries[key] = self._add_entry(cat, icon, title, desc, sw)
        self._hardening_toggle = ToggleSwitch(self)
        self._hardening_toggle.toggled.connect(self._on_field_changed)
        self._hardening_entry = self._add_entry(
            cat, "security-high", "Process hardening",
            "Resource limits, disabled ptrace, malloc hardening.",
            self._hardening_toggle,
        )
        self._private_tmp_toggle = ToggleSwitch(self)
        self._private_tmp_toggle.toggled.connect(self._on_field_changed)
        self._private_tmp_entry = self._add_entry(
            cat, "folder-temp", "Private /tmp",
            "Give the app its own empty /tmp.",
            self._private_tmp_toggle,
        )

    def _load_widget_state(self):
        p = self._policy
        self._master_toggle.setChecked(p.enabled)
        idx = self._backend_combo.findData(p.backend)
        if idx < 0:
            idx = self._backend_combo.findData("shield")
        self._backend_combo.setCurrentIndex(max(idx, 0))
        self._home_combo.setCurrentIndex(self._mode_index(p.home_access))
        for key in _DIR_META:
            combo = getattr(self, f"_dir_combo_{key}")
            combo.setCurrentIndex(self._mode_index(p.dirs.get(key, "rw")))
        self._removable_combo.setCurrentIndex(self._mode_index(p.removable))
        self._network_toggle.setChecked(p.network)
        for key, sw in self._device_toggles.items():
            sw.setChecked(bool(p.devices.get(key, False)))
        for key, sw in self._ipc_toggles.items():
            sw.setChecked(bool(p.ipc.get(key, False)))
        self._hardening_toggle.setChecked(p.hardening)
        self._private_tmp_toggle.setChecked(p.private_tmp)

    @staticmethod
    def _mode_index(mode: str) -> int:
        for i, (_label, m) in enumerate(_MODE_LABELS):
            if m == mode:
                return i
        return 0

    @staticmethod
    def _mode_from_index(index: int) -> str:
        return _MODE_LABELS[index][1]

    def _collect(self) -> SandboxPolicy:
        p = deepcopy(self._policy)
        p.enabled = self._master_toggle.isChecked()
        p.backend = self._backend_combo.currentData() or "shield"
        p.home_access = self._mode_from_index(self._home_combo.currentIndex())
        for key in _DIR_META:
            combo = getattr(self, f"_dir_combo_{key}")
            p.dirs[key] = self._mode_from_index(combo.currentIndex())
        p.removable = self._mode_from_index(self._removable_combo.currentIndex())
        p.network = self._network_toggle.isChecked()
        for key, sw in self._device_toggles.items():
            p.devices[key] = sw.isChecked()
        for key, sw in self._ipc_toggles.items():
            p.ipc[key] = sw.isChecked()
        p.hardening = self._hardening_toggle.isChecked()
        p.private_tmp = self._private_tmp_toggle.isChecked()
        p.app_id = self._app_name
        return p

    def _refresh_gating(self):
        master = self._master_toggle.isChecked()
        backend = self._backend()

        fs_cap = backend.capability("filesystem")
        network_cap = backend.capability("network")
        dev_cap = backend.capability("devices")
        ipc_cap = backend.capability("ipc")
        res_cap = backend.capability("resources")

        self._gate_entry(self._home_entry, master, fs_cap)
        for key in _DIR_META:
            self._gate_entry(self._dir_entries[key], master, fs_cap)
        self._gate_entry(self._removable_entry, master, fs_cap)

        self._gate_entry(self._network_entry, master, network_cap)

        for key, entry in self._device_entries.items():
            self._gate_entry(entry, master, dev_cap)

        for key, entry in self._ipc_entries.items():
            self._gate_entry(entry, master, ipc_cap)

        self._gate_entry(self._hardening_entry, master, res_cap)
        self._gate_entry(self._private_tmp_entry, master, fs_cap)

        self._refresh_engine_state()
        self._refresh_footer()

    def _gate_entry(self, entry: _Entry, master: bool, cap, tweak: str | None = None):
        if entry is None:
            return
        if not master:
            entry.set_gating(False, "Enable isolation first", None)
            return
        detail = cap.detail if cap.detail else ""
        text = _LEVEL_WORD.get(cap.level, "Unknown")
        if detail and tweak:
            text = f"— {tweak}: {detail}"
        elif detail:
            text = f"{text}: {detail}"
        entry.set_gating(cap.level != UNSUPPORTED, text if not tweak else tweak, _LEVEL_COLOR.get(cap.level, COLOR_WARNING))

    def _on_master_toggled(self, checked: bool):
        self._on_field_changed()
        self._refresh_gating()

    def _on_backend_selected(self, _idx: int):
        self._on_field_changed()
        self._refresh_gating()

    def _on_field_changed(self):
        self.policy_changed.emit()
        self._refresh_footer()

    def _refresh_footer(self):
        proposed = self._collect()
        dirty = proposed != self._original
        self._btn_apply.setEnabled(dirty)
        self._btn_discard.setEnabled(dirty)
        self._btn_reset.setEnabled(proposed != preset_default(self._app_name))
        self._dirty_label.setText("Unsaved changes — take effect on next launch." if dirty else "")

    def _build_bar(self) -> QWidget:
        bar = QFrame()
        bar.setFrameShape(QFrame.Shape.StyledPanel)
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(0, 0, 0, 0)
        bar_lay.setSpacing(6)
        self._dirty_label = QLabel()
        self._dirty_label.setStyleSheet(placeholder_style("", 10))
        bar_lay.addWidget(self._dirty_label, 1)
        self._btn_reset = QPushButton(get_icon("document-revert"), "Reset")
        self._btn_reset.clicked.connect(self._reset_defaults)
        bar_lay.addWidget(self._btn_reset)
        self._btn_discard = QPushButton(get_icon("edit-undo"), "Discard")
        self._btn_discard.clicked.connect(self._discard)
        bar_lay.addWidget(self._btn_discard)
        self._btn_apply = QPushButton(get_icon("check"), "Apply")
        self._btn_apply.setProperty("variant", "primary")
        self._btn_apply.clicked.connect(self._apply)
        bar_lay.addWidget(self._btn_apply)
        return bar

    def _refresh_engine_state(self):
        if not self._master_toggle.isChecked():
            self._enforcement_label.setText("Isolation off — app runs with full user access.")
            self._enforcement_label.setStyleSheet(f"color: {COLOR_ERROR}; font-size: 10px;")
            return
        backend = self._backend()
        state = backend.get_enforcement_status(self._collect())
        label = state.label
        text = f"{label} ({backend.display_name})"
        if state.detail:
            text += f" — {state.detail}"
        if self._verification_cache:
            verdict = self._verification_cache.get("summary")
            if verdict == "verified":
                text += "\nVerified by real probes."
            elif verdict in ("partial", "not-verified", "failed"):
                text += f"\nVerification: {verdict}"
        else:
            text += "\nStates are configuration-derived; not verified by probes."
        color = {
            "sandboxed": COLOR_SUCCESS,
            "partially_restricted": COLOR_WARNING,
            "not_sandboxed": COLOR_ERROR,
            "configuration_error": COLOR_ERROR,
            "backend_unavailable": COLOR_ERROR,
            "pending_changes": COLOR_WARNING,
        }.get(state.state, COLOR_WARNING)
        self._enforcement_label.setText(text)
        self._enforcement_label.setStyleSheet(f"color: {color}; font-size: 10px;")

    def _run_verification(self):
        play_sound("click")
        from niruvi.core.sandbox_diagnostics import verify_sandbox

        policy = self._collect()
        self._verify_btn.setEnabled(False)
        self._verify_btn.setText("Verifying…")
        try:
            self._verification_cache = verify_sandbox(
                policy, self._info.get("path", ""), {}
            )
        except Exception as e:
            logger.debug("verification crashed: %s", e)
            self._verification_cache = None
        finally:
            self._verify_btn.setEnabled(True)
            self._verify_btn.setText("Verify")
        self._refresh_engine_state()

    def _toggle_advanced(self):
        self._adv_details.setVisible(self._adv_btn.isChecked())

    def _refresh_advanced(self):
        if self._adv_btn.isChecked():
            self._adv_details.setText("\n".join(describe_policy(self._collect())))

    def _reset_defaults(self):
        play_sound("click")
        preset_name = self._backend_combo.currentData() or "shield"
        self._policy = preset_default(self._app_name, preset_name)
        self._load_widget_state()
        self._refresh_gating()

    def _discard(self):
        play_sound("click")
        self._policy = deepcopy(self._original)
        self._load_widget_state()
        self._refresh_gating()

    def _apply(self):
        proposed = self._collect()
        issues = proposed.validate()
        if issues:
            play_sound("error")
            QMessageBox.warning(self, "Fix First", "\n".join(issues))
            return
        backend = get_backend(proposed.backend, strict=False)
        preview = diff_policies(self._original, proposed)["changes"]
        risk = dangerous_changes(proposed)
        msg = "Apply these permission changes?"
        if preview:
            msg += "\n\n" + "\n".join(preview[:25])
        if risk:
            msg += "\n\nDangerous:\n" + "\n".join(risk)
        play_sound("click")
        reply = QMessageBox.question(
            self, "Apply Changes", msg,
            QMessageBox.StandardButton.Apply | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Apply,
        )
        if reply != QMessageBox.StandardButton.Apply:
            return
        result = backend.apply_policy(self._app_name, proposed, record=self._record)
        if result.ok:
            self._original = deepcopy(proposed)
            self._policy = deepcopy(proposed)
            try:
                self._record = InstallationRegistry().get(self._app_name)
            except Exception as e:
                logger.debug("Could not re-read registry record: %s", e)
            self._refresh_gating()
            play_sound("success")
            self._dirty_label.setText("Changes applied — take effect on next launch.")
            if result.warnings:
                self._dirty_label.setText("Changes applied — some not enforced: " + "; ".join(result.warnings))
            self.policy_changed.emit()
        else:
            play_sound("error")
            QMessageBox.critical(self, "Apply Failed", result.error or "Unknown failure.")
            self._dirty_label.setText("Apply failed — previous policy still active.")
            try:
                backend.restore_previous_policy(self._app_name, self._original, record=self._record)
            except Exception as e:
                logger.debug("Rollback after failed apply did not run: %s", e, exc_info=True)