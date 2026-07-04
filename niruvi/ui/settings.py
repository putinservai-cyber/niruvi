import logging
import os
import subprocess

logger = logging.getLogger(__name__)

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from niruvi.config import (  # noqa: F401 — re-exported for other UI modules
    DEFAULT_INSTALL_DIR,
    DESKTOP_DIR,
    INSTALLED_DIR,
    _settings,
    get_data_dir,
    get_settings,
    load_settings,
    save_settings,
)
from niruvi.ui.toggle_switch import ToggleSwitch
from niruvi.utils import get_icon
from niruvi.utils.sound_manager import play as play_sound
from niruvi.utils.styles import placeholder_style
from niruvi.utils.theme_engine import COLOR_SUCCESS


def _is_local_path(path: str) -> bool:
    reject_prefixes = ("mtp:", "gvfs", "/media/", "/run/media/", "/mnt/")
    resolved = os.path.realpath(os.path.expanduser(path))
    if not os.path.isabs(resolved):
        return False
    for p in reject_prefixes:
        if p.startswith("/") and resolved.startswith(p):
            return False
        if p in resolved:
            return False
    return True


class _ToggleRow(QWidget):
    def __init__(self, label: str, tooltip: str = "", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        self.label = QLabel(label)
        self.label.setWordWrap(True)
        self.label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.toggle = ToggleSwitch(self)
        if tooltip:
            self.label.setToolTip(tooltip)
            self.toggle.setToolTip(tooltip)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.toggle)
        self.setToolTip(tooltip)

    def isChecked(self):
        return self.toggle.isChecked()

    def setChecked(self, checked: bool):
        self.toggle.setChecked(checked)


class _SoundCategoryRow(QWidget):
    _PREVIEW_KEYS: dict[str, str] = {
        "feedback": "click",
        "navigation": "interface",
        "notifications": "info",
    }

    def __init__(self, label: str, category: str, tooltip: str = "", parent=None):
        super().__init__(parent)
        self._category = category
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        self.label = QLabel(label)
        self.label.setWordWrap(True)
        self.label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        if tooltip:
            self.label.setToolTip(tooltip)
        self.toggle = ToggleSwitch(self)
        if tooltip:
            self.toggle.setToolTip(tooltip)
        self.preview_btn = QPushButton("\u25B6")
        self.preview_btn.setFixedSize(32, 24)
        self.preview_btn.setToolTip("Preview this sound category")
        self.preview_btn.clicked.connect(self._preview)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.preview_btn)
        layout.addWidget(self.toggle)
        self.setToolTip(tooltip)

    def _preview(self):
        key = self._PREVIEW_KEYS.get(self._category, "click")
        play_sound(key)

    def isChecked(self):
        return self.toggle.isChecked()

    def setChecked(self, checked: bool):
        self.toggle.setChecked(checked)


class SettingsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setSpacing(16)
        layout.setContentsMargins(16, 12, 16, 12)

        title = QLabel("<b>Settings</b>")
        font = title.font()
        font.setPointSize(16)
        title.setFont(font)
        layout.addWidget(title)

        form = QFormLayout()
        self.install_dir_edit = QLineEdit(_settings.get("install_dir", DEFAULT_INSTALL_DIR))
        self.install_dir_edit.setReadOnly(True)
        browse_btn = QPushButton(get_icon("folder-open"), "Browse...")
        browse_btn.clicked.connect(lambda: (play_sound("click"), self._browse_install_dir()))
        dir_layout = QHBoxLayout()
        dir_layout.addWidget(self.install_dir_edit)
        dir_layout.addWidget(browse_btn)
        form.addRow("Installation directory:", dir_layout)
        layout.addLayout(form)

        defaults_group = QGroupBox("Installation defaults")
        defaults_layout = QVBoxLayout(defaults_group)
        defaults_layout.setSpacing(2)

        self.create_desktop_row = _ToggleRow(
            "Create desktop entries (show in app menu)",
            "When enabled, a .desktop file will be created in "
            "~/.local/share/applications so the app appears in your DE's launcher",
        )
        self.create_desktop_row.setChecked(_settings.get("create_desktop", True))
        defaults_layout.addWidget(self.create_desktop_row)

        self.shortcut_row = _ToggleRow(
            "Create desktop shortcut", "When enabled, a shortcut icon will be placed on your Desktop"
        )
        self.shortcut_row.setChecked(_settings.get("create_shortcut", False))
        defaults_layout.addWidget(self.shortcut_row)

        self.portable_home_row = _ToggleRow(
            "Create portable home folder", "Creates a .home folder next to the app for persistent user data"
        )
        self.portable_home_row.setChecked(_settings.get("portable_home", False))
        defaults_layout.addWidget(self.portable_home_row)

        self.portable_config_row = _ToggleRow(
            "Create portable config folder", "Creates a .config folder next to the app for persistent configuration"
        )
        self.portable_config_row.setChecked(_settings.get("portable_config", False))
        defaults_layout.addWidget(self.portable_config_row)

        self.auto_scan_row = _ToggleRow(
            "Auto-scan before install", "Runs a security scan on every AppImage before installing it"
        )
        self.auto_scan_row.setChecked(_settings.get("auto_scan_before_install", True))
        defaults_layout.addWidget(self.auto_scan_row)

        layout.addWidget(defaults_group)

        shield_group = QGroupBox("Process Isolation")
        shield_layout = QVBoxLayout(shield_group)
        shield_layout.setSpacing(2)

        self.shield_enabled_row = _ToggleRow(
            "Enable process hardening for new installations",
            "Applies rlimits, memory locking, ptrace disable, and malloc hardening",
        )
        self.shield_enabled_row.setChecked(_settings.get("sandbox_default_enabled", True))
        shield_layout.addWidget(self.shield_enabled_row)

        avail = self._detect_sandbox_status()
        if avail:
            status_label = QLabel(f"Available: {avail}")
            status_label.setStyleSheet(f"color: {COLOR_SUCCESS}; font-size: 11px;")
        else:
            status_label = QLabel("Process hardening not available")
            status_label.setStyleSheet(placeholder_style("", 11))
        status_label.setWordWrap(True)
        shield_layout.addWidget(status_label)

        backend_row = QHBoxLayout()
        backend_row.addWidget(QLabel("Default backend:"))
        self.backend_combo = QComboBox()
        self.backend_combo.addItem("Niruvi Shield", "shield")
        backends_info = self._detect_backend_details()
        if backends_info.get("firejail"):
            self.backend_combo.addItem("Firejail", "firejail")
        if backends_info.get("bwrap"):
            self.backend_combo.addItem("Bubblewrap", "bwrap")
        saved = _settings.get("sandbox_default_backend", "shield")
        for i in range(self.backend_combo.count()):
            if self.backend_combo.itemData(i) == saved:
                self.backend_combo.setCurrentIndex(i)
                break
        backend_row.addWidget(self.backend_combo)
        backend_row.addStretch()
        shield_layout.addLayout(backend_row)

        layout.addWidget(shield_group)

        update_group = QGroupBox("Background Updates")
        update_layout = QVBoxLayout(update_group)
        update_layout.setSpacing(2)

        self.auto_update_apps_row = _ToggleRow(
            "Auto-update apps in background",
            "When enabled, Niruvi periodically checks all apps that have auto-update "
            "enabled and notifies you of available updates",
        )
        self.auto_update_apps_row.setChecked(_settings.get("auto_update_apps", False))
        update_layout.addWidget(self.auto_update_apps_row)

        interval_row = QHBoxLayout()
        interval_row.addWidget(QLabel("Check interval:"))
        self.update_interval_combo = QComboBox()
        self.update_interval_combo.addItems(["daily", "weekly", "monthly"])
        current_interval = _settings.get("update_check_interval", "weekly")
        idx = self.update_interval_combo.findText(current_interval)
        if idx >= 0:
            self.update_interval_combo.setCurrentIndex(idx)
        interval_row.addWidget(self.update_interval_combo)
        interval_row.addStretch()
        update_layout.addLayout(interval_row)

        layout.addWidget(update_group)

        hooks_group = QGroupBox("Hooks")
        hooks_layout = QVBoxLayout(hooks_group)
        hooks_layout.setSpacing(4)

        hooks_dir = os.path.expanduser("~/.config/niruvi/hooks")
        os.makedirs(hooks_dir, exist_ok=True)
        hooks_label = QLabel(f"Hooks directory:<br><code>{hooks_dir}</code>")
        hooks_label.setWordWrap(True)
        hooks_label.setStyleSheet(placeholder_style("", 12))
        hooks_layout.addWidget(hooks_label)

        hooks_desc = QLabel(
            "Place <code>.hook</code> scripts in the hooks directory (or a subdirectory "
            "matching an app name) to run them before the app launches. "
            "Scripts receive <code>APP_NAME</code> and <code>APP_DIR</code> environment variables."
        )
        hooks_desc.setWordWrap(True)
        hooks_desc.setStyleSheet(placeholder_style("", 11))
        hooks_layout.addWidget(hooks_desc)

        open_hooks_btn = QPushButton(get_icon("folder-open"), "Open Hooks Directory")
        open_hooks_btn.clicked.connect(lambda: (play_sound("click"), subprocess.Popen(["xdg-open", hooks_dir], start_new_session=True)))
        hooks_layout.addWidget(open_hooks_btn)

        layout.addWidget(hooks_group)

        icon_group = QGroupBox("Icons")
        icon_layout = QVBoxLayout(icon_group)

        self.icon_theme_radio = QRadioButton("Install icon to theme directory (recommended)")
        self.icon_theme_radio.setChecked(_settings.get("icon_in_theme", True))
        self.icon_theme_radio.setToolTip("Copies the icon to ~/.local/share/icons/hicolor/ so all DEs can find it")
        icon_layout.addWidget(self.icon_theme_radio)

        self.icon_absolute_radio = QRadioButton("Use absolute path to icon inside app dir")
        self.icon_absolute_radio.setChecked(not _settings.get("icon_in_theme", True))
        self.icon_absolute_radio.setToolTip(
            "Points Icon= directly at the file inside the app directory.\n"
            "Icon breaks if the app directory is moved or renamed."
        )
        icon_layout.addWidget(self.icon_absolute_radio)

        layout.addWidget(icon_group)

        tn_group = QGroupBox("File Manager Thumbnailer")
        tn_layout = QVBoxLayout(tn_group)
        tn_layout.setSpacing(4)

        self.tn_status_label = QLabel()
        tn_layout.addWidget(self.tn_status_label)

        tn_btn_row = QHBoxLayout()
        self.btn_install_tn = QPushButton(get_icon("emblem-photos", "image-x-generic"), "Install Thumbnailer")
        self.btn_install_tn.clicked.connect(lambda: (play_sound("click"), self._install_thumbnailer()))
        tn_btn_row.addWidget(self.btn_install_tn)
        self.btn_remove_tn = QPushButton(get_icon("edit-delete"), "Remove Thumbnailer")
        self.btn_remove_tn.clicked.connect(lambda: (play_sound("click"), self._remove_thumbnailer()))
        tn_btn_row.addWidget(self.btn_remove_tn)
        tn_btn_row.addStretch()
        tn_layout.addLayout(tn_btn_row)

        tn_info = QLabel(
            "Shows AppImage icons as thumbnails in file managers "
            "(Nautilus, Nemo, Thunar, Dolphin). "
            "Requires tumbler or GNOME thumbnails daemon."
        )
        tn_info.setWordWrap(True)
        tn_info.setStyleSheet(placeholder_style("", 11))
        tn_layout.addWidget(tn_info)

        layout.addWidget(tn_group)

        self._update_thumbnailer_status()

        theme_group = QGroupBox("Appearance")
        theme_layout = QVBoxLayout(theme_group)
        theme_layout.setSpacing(4)

        self.theme_combo = QComboBox()
        self.theme_combo.addItem("System (auto-detect)", "auto")
        self.theme_combo.addItem("Light", "light")
        self.theme_combo.addItem("Dark", "dark")
        current_theme = _settings.get("theme_mode", "auto")
        idx = self.theme_combo.findData(current_theme)
        if idx >= 0:
            self.theme_combo.setCurrentIndex(idx)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel("Theme:"))
        theme_row.addWidget(self.theme_combo)
        theme_row.addStretch()
        theme_layout.addLayout(theme_row)

        theme_desc = QLabel("Detects your desktop environment's light or dark theme automatically.")
        theme_desc.setWordWrap(True)
        theme_desc.setStyleSheet(placeholder_style("", 11))
        theme_layout.addWidget(theme_desc)

        layout.addWidget(theme_group)

        storage_group = QGroupBox("Storage")
        storage_layout = QVBoxLayout(storage_group)
        storage_layout.setSpacing(4)

        self.remove_source_row = _ToggleRow(
            "Delete source AppImage after successful installation",
            "When enabled, the original .AppImage file is deleted after it is "
            "successfully installed. Helps prevent duplicate files and saves disk space. "
            "Downloaded files from the catalog are always temporary.",
        )
        self.remove_source_row.setChecked(_settings.get("auto_remove_source", False))
        storage_layout.addWidget(self.remove_source_row)

        layout.addWidget(storage_group)

        audio_group = QGroupBox("Sound Effects")
        audio_layout = QVBoxLayout(audio_group)
        audio_layout.setSpacing(4)

        self.sound_effects_row = _ToggleRow(
            "Play sound effects", "Play sounds for installation, errors, and navigation"
        )
        self.sound_effects_row.setChecked(_settings.get("sound_effects_enabled", True))
        self.sound_effects_row.toggle.toggled.connect(
            lambda checked: _settings.update({"sound_effects_enabled": checked})
        )
        audio_layout.addWidget(self.sound_effects_row)

        vol_row = QHBoxLayout()
        vol_row.addWidget(QLabel("Master volume:"))
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        vol_val = int(_settings.get("sound_volume", 0.7) * 100)
        self.volume_slider.setValue(vol_val)
        self.volume_slider.setToolTip("Adjust the master volume of all sound effects")
        self.volume_label = QLabel(f"{vol_val}%")
        self.volume_label.setFixedWidth(36)
        self.volume_slider.valueChanged.connect(lambda v: self.volume_label.setText(f"{v}%"))
        vol_row.addWidget(self.volume_slider)
        vol_row.addWidget(self.volume_label)
        audio_layout.addLayout(vol_row)

        self.sound_feedback_row = _SoundCategoryRow(
            "Feedback sounds", "feedback",
            "Click, success, error, warning, toggle sounds",
        )
        self.sound_feedback_row.setChecked(_settings.get("sound_feedback_enabled", True))
        self.sound_feedback_row.toggle.toggled.connect(
            lambda checked: _settings.update({"sound_feedback_enabled": checked})
        )
        audio_layout.addWidget(self.sound_feedback_row)

        fb_vol_row = QHBoxLayout()
        fb_vol_row.addWidget(QLabel("  Feedback volume:"))
        self.feedback_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.feedback_volume_slider.setRange(0, 100)
        fb_vol_val = int(_settings.get("sound_volume_feedback", 1.0) * 100)
        self.feedback_volume_slider.setValue(fb_vol_val)
        self.feedback_volume_label = QLabel(f"{fb_vol_val}%")
        self.feedback_volume_label.setFixedWidth(36)
        self.feedback_volume_slider.valueChanged.connect(lambda v: self.feedback_volume_label.setText(f"{v}%"))
        fb_vol_row.addWidget(self.feedback_volume_slider)
        fb_vol_row.addWidget(self.feedback_volume_label)
        audio_layout.addLayout(fb_vol_row)

        self.sound_navigation_row = _SoundCategoryRow(
            "Navigation sounds", "navigation",
            "Interface open, page transition sounds",
        )
        self.sound_navigation_row.setChecked(_settings.get("sound_navigation_enabled", True))
        self.sound_navigation_row.toggle.toggled.connect(
            lambda checked: _settings.update({"sound_navigation_enabled": checked})
        )
        audio_layout.addWidget(self.sound_navigation_row)

        nav_vol_row = QHBoxLayout()
        nav_vol_row.addWidget(QLabel("  Navigation volume:"))
        self.nav_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.nav_volume_slider.setRange(0, 100)
        nav_vol_val = int(_settings.get("sound_volume_navigation", 1.0) * 100)
        self.nav_volume_slider.setValue(nav_vol_val)
        self.nav_volume_label = QLabel(f"{nav_vol_val}%")
        self.nav_volume_label.setFixedWidth(36)
        self.nav_volume_slider.valueChanged.connect(lambda v: self.nav_volume_label.setText(f"{v}%"))
        nav_vol_row.addWidget(self.nav_volume_slider)
        nav_vol_row.addWidget(self.nav_volume_label)
        audio_layout.addLayout(nav_vol_row)

        self.sound_notifications_row = _SoundCategoryRow(
            "Notification sounds", "notifications",
            "Info alerts and notification beeps",
        )
        self.sound_notifications_row.setChecked(_settings.get("sound_notifications_enabled", True))
        self.sound_notifications_row.toggle.toggled.connect(
            lambda checked: _settings.update({"sound_notifications_enabled": checked})
        )
        audio_layout.addWidget(self.sound_notifications_row)

        notif_vol_row = QHBoxLayout()
        notif_vol_row.addWidget(QLabel("  Notification volume:"))
        self.notif_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.notif_volume_slider.setRange(0, 100)
        notif_vol_val = int(_settings.get("sound_volume_notifications", 1.0) * 100)
        self.notif_volume_slider.setValue(notif_vol_val)
        self.notif_volume_label = QLabel(f"{notif_vol_val}%")
        self.notif_volume_label.setFixedWidth(36)
        self.notif_volume_slider.valueChanged.connect(lambda v: self.notif_volume_label.setText(f"{v}%"))
        notif_vol_row.addWidget(self.notif_volume_slider)
        notif_vol_row.addWidget(self.notif_volume_label)
        audio_layout.addLayout(notif_vol_row)

        layout.addWidget(audio_group)

        privacy_group = QGroupBox("Privacy")
        privacy_layout = QVBoxLayout(privacy_group)
        privacy_layout.setSpacing(4)

        self.privacy_updates_row = _ToggleRow(
            "Check for updates automatically",
            "When enabled, Niruvi will periodically check GitHub for new versions. "
            "No personal data is transmitted.",
        )
        self.privacy_updates_row.setChecked(_settings.get("privacy_allow_update_checks", True))
        privacy_layout.addWidget(self.privacy_updates_row)

        privacy_note = QLabel(
            "Update checks send only your current app version number to GitHub's public API. "
            "No personal data, paths, or system information is transmitted."
        )
        privacy_note.setWordWrap(True)
        privacy_note.setStyleSheet(placeholder_style("", 10))
        privacy_layout.addWidget(privacy_note)

        layout.addWidget(privacy_group)

        help_label = QLabel("Theme changes apply immediately. Other changes apply on the next install or build.")
        help_label.setStyleSheet(placeholder_style("", 11))
        help_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(help_label)

        layout.addStretch()

    def _detect_sandbox_status(self) -> str:
        try:
            from niruvi.core.sandbox import check_shield_available

            info = check_shield_available()
            parts = []
            if info.get("hardening"):
                parts.append("Niruvi Shield")
            backends = info.get("backends", {})
            if backends.get("firejail"):
                ver = info.get("firejail_version", "")
                parts.append(f"Firejail {ver}" if ver else "Firejail")
            if backends.get("bwrap"):
                ver = info.get("bwrap_version", "")
                parts.append(f"Bubblewrap {ver}" if ver else "Bubblewrap")
            if info.get("portable_mode"):
                parts.append("Portable mode")
            if info.get("xdg_open_daemon"):
                parts.append("xdg-open proxy")
            return " | ".join(parts) if parts else ""
        except Exception as e:
            logger.debug("Failed to detect sandbox status: %s", e, exc_info=True)
            return ""

    def _detect_backend_details(self) -> dict:
        try:
            from niruvi.core.sandbox import check_bwrap_available, check_firejail_available

            return {
                "firejail": check_firejail_available().get("available", False),
                "bwrap": check_bwrap_available().get("available", False),
            }
        except Exception as e:
            logger.debug("Failed to detect backend details: %s", e, exc_info=True)
            return {"firejail": False, "bwrap": False}

    def _update_thumbnailer_status(self):
        from niruvi.desktop.thumbnailer import check_thumbnailer_installed

        if check_thumbnailer_installed():
            self.tn_status_label.setText("Thumbnailer is installed")
            self.tn_status_label.setStyleSheet(f"color: {COLOR_SUCCESS};")
            self.btn_install_tn.setEnabled(False)
            self.btn_remove_tn.setEnabled(True)
        else:
            self.tn_status_label.setText("Not installed — AppImages won't show icons in file managers")
            self.tn_status_label.setStyleSheet("color: palette(placeholderText);")
            self.btn_install_tn.setEnabled(True)
            self.btn_remove_tn.setEnabled(False)

    def _install_thumbnailer(self):
        from niruvi.desktop.thumbnailer import install_thumbnailer

        err = install_thumbnailer()
        if err:
            play_sound("error")
            QMessageBox.critical(self, "Install Failed", err)
        else:
            play_sound("success")
            QMessageBox.information(
                self,
                "Thumbnailer Installed",
                "The AppImage thumbnailer has been installed.\n\n"
                "You may need to restart your file manager "
                "or log out and back in for changes to take effect.",
            )
        self._update_thumbnailer_status()

    def _remove_thumbnailer(self):
        from niruvi.desktop.thumbnailer import remove_thumbnailer

        err = remove_thumbnailer()
        if err:
            play_sound("error")
            QMessageBox.critical(self, "Remove Failed", err)
        else:
            play_sound("success")
            QMessageBox.information(self, "Thumbnailer Removed", "The thumbnailer has been removed.")
        self._update_thumbnailer_status()

    def _browse_install_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select installation directory", self.install_dir_edit.text())
        if dir_path:
            if not _is_local_path(dir_path):
                play_sound("warning")
                QMessageBox.warning(
                    self,
                    "Invalid Path",
                    "Cannot use a removable drive or phone path as the install directory.<br><br>"
                    "Please choose a folder on your local filesystem (e.g. <code>~/Applications</code>).",
                )
                return
            self.install_dir_edit.setText(dir_path)

    def _on_theme_changed(self):
        from niruvi.utils.theme_engine import ThemeMode, get_theme_engine

        mode_map = {"auto": ThemeMode.AUTO, "light": ThemeMode.LIGHT, "dark": ThemeMode.DARK}
        mode = mode_map.get(self.theme_combo.currentData(), ThemeMode.AUTO)
        get_theme_engine().mode = mode

    def _has_changes(self) -> bool:
        return (
            self.install_dir_edit.text() != _settings.get("install_dir", DEFAULT_INSTALL_DIR)
            or self.create_desktop_row.isChecked() != _settings.get("create_desktop", True)
            or self.shortcut_row.isChecked() != _settings.get("create_shortcut", False)
            or self.portable_home_row.isChecked() != _settings.get("portable_home", False)
            or self.portable_config_row.isChecked() != _settings.get("portable_config", False)
            or self.auto_scan_row.isChecked() != _settings.get("auto_scan_before_install", True)
            or self.auto_update_apps_row.isChecked() != _settings.get("auto_update_apps", False)
            or self.update_interval_combo.currentText() != _settings.get("update_check_interval", "weekly")
            or self.icon_theme_radio.isChecked() != _settings.get("icon_in_theme", True)
            or self.shield_enabled_row.isChecked() != _settings.get("sandbox_default_enabled", True)
            or self.backend_combo.currentData() != _settings.get("sandbox_default_backend", "shield")
            or self.remove_source_row.isChecked() != _settings.get("auto_remove_source", False)
            or self.sound_effects_row.isChecked() != _settings.get("sound_effects_enabled", True)
            or self.volume_slider.value() != int(_settings.get("sound_volume", 0.7) * 100)
            or self.sound_feedback_row.isChecked() != _settings.get("sound_feedback_enabled", True)
            or self.sound_navigation_row.isChecked() != _settings.get("sound_navigation_enabled", True)
            or self.sound_notifications_row.isChecked() != _settings.get("sound_notifications_enabled", True)
            or self.feedback_volume_slider.value() != int(_settings.get("sound_volume_feedback", 1.0) * 100)
            or self.nav_volume_slider.value() != int(_settings.get("sound_volume_navigation", 1.0) * 100)
            or self.notif_volume_slider.value() != int(_settings.get("sound_volume_notifications", 1.0) * 100)
            or self.privacy_updates_row.isChecked() != _settings.get("privacy_allow_update_checks", True)
            or self.theme_combo.currentData() != _settings.get("theme_mode", "auto")
        )

    def apply(self) -> bool:
        install_dir = self.install_dir_edit.text()
        if not _is_local_path(install_dir):
            play_sound("warning")
            QMessageBox.warning(
                self,
                "Invalid Path",
                "Cannot set install directory to a removable drive or phone path.<br><br>Reverting to previous value.",
            )
            self.install_dir_edit.setText(_settings.get("install_dir", DEFAULT_INSTALL_DIR))
            return False
        _settings["install_dir"] = install_dir
        _settings["create_desktop"] = self.create_desktop_row.isChecked()
        _settings["create_shortcut"] = self.shortcut_row.isChecked()
        _settings["portable_home"] = self.portable_home_row.isChecked()
        _settings["portable_config"] = self.portable_config_row.isChecked()
        _settings["auto_scan_before_install"] = self.auto_scan_row.isChecked()
        _settings["auto_update_apps"] = self.auto_update_apps_row.isChecked()
        _settings["update_check_interval"] = self.update_interval_combo.currentText()
        _settings["icon_in_theme"] = self.icon_theme_radio.isChecked()
        _settings["sandbox_default_enabled"] = self.shield_enabled_row.isChecked()
        _settings["sandbox_default_backend"] = self.backend_combo.currentData()
        _settings["auto_remove_source"] = self.remove_source_row.isChecked()
        _settings["sound_effects_enabled"] = self.sound_effects_row.isChecked()
        _settings["sound_volume"] = self.volume_slider.value() / 100.0
        _settings["sound_feedback_enabled"] = self.sound_feedback_row.isChecked()
        _settings["sound_navigation_enabled"] = self.sound_navigation_row.isChecked()
        _settings["sound_notifications_enabled"] = self.sound_notifications_row.isChecked()
        _settings["sound_volume_feedback"] = self.feedback_volume_slider.value() / 100.0
        _settings["sound_volume_navigation"] = self.nav_volume_slider.value() / 100.0
        _settings["sound_volume_notifications"] = self.notif_volume_slider.value() / 100.0
        _settings["privacy_allow_update_checks"] = self.privacy_updates_row.isChecked()
        _settings["theme_mode"] = self.theme_combo.currentData()
        save_settings()
        return True


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Niruvi Settings")
        self.setMinimumSize(460, 400)
        self._page = SettingsPage(self)
        layout = QVBoxLayout(self)
        layout.addWidget(self._page)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        if self._page.apply():
            play_sound("click")
            super().accept()

    def reject(self):
        if self._page._has_changes():
            play_sound("warning")
            reply = QMessageBox.question(
                self,
                "Unsaved Changes",
                "You have unsaved changes. Discard them?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        play_sound("navigation")
        super().reject()
