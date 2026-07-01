import json
import logging
import os
import subprocess

from PyQt6.QtCore import Qt
from niruvi.utils import get_icon
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QPushButton, QDialogButtonBox,
    QFileDialog, QGroupBox, QRadioButton, QLabel, QScrollArea,
    QFrame, QSizePolicy, QMessageBox, QComboBox,
)

from niruvi.toggle_switch import ToggleSwitch

DEFAULT_INSTALL_DIR = os.path.expanduser("~/Applications")
DESKTOP_DIR = os.path.expanduser("~/.local/share/applications")

INSTALLED_DIR = os.path.expanduser("~/Applications/Niruvi")


def get_data_dir():
    env = os.environ.get("NIRUVI_DATA_DIR")
    if env:
        return env
    if os.path.isfile(os.path.join(INSTALLED_DIR, "AppRun")):
        return os.path.join(INSTALLED_DIR, ".niruvi")
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        return os.path.join(os.path.dirname(appimage), ".niruvi")
    return os.path.expanduser("~/.config/niruvi")


_settings = {
    "install_dir": DEFAULT_INSTALL_DIR,
    "create_desktop": True,
    "create_shortcut": False,
    "portable_home": False,
    "portable_config": False,
    "icon_in_theme": True,
    "auto_scan_before_install": True,
    "update_check_interval": "weekly",
    "auto_update_apps": False,
    "sandbox_default_enabled": True,
    "sandbox_default_level": 2,
    "sandbox_default_backend": "shield",
    "auto_remove_source": False,
    "sound_effects_enabled": True,
}


def get_settings():
    return _settings


def _settings_file():
    return os.path.join(get_data_dir(), "settings.json")


def load_settings():
    sf = _settings_file()
    if os.path.exists(sf):
        try:
            with open(sf) as f:
                loaded = json.load(f)
                _settings.update(loaded)
        except (json.JSONDecodeError, OSError) as e:
            logging.warning("Corrupted settings file: %s", e)


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


def save_settings():
    data_dir = get_data_dir()
    os.makedirs(data_dir, exist_ok=True)
    with open(_settings_file(), "w") as f:
        json.dump(_settings, f, indent=2)


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
        browse_btn.clicked.connect(self._browse_install_dir)
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
            "~/.local/share/applications so the app appears in your DE's launcher"
        )
        self.create_desktop_row.setChecked(_settings.get("create_desktop", True))
        defaults_layout.addWidget(self.create_desktop_row)

        self.shortcut_row = _ToggleRow(
            "Create desktop shortcut",
            "When enabled, a shortcut icon will be placed on your Desktop"
        )
        self.shortcut_row.setChecked(_settings.get("create_shortcut", False))
        defaults_layout.addWidget(self.shortcut_row)

        self.portable_home_row = _ToggleRow(
            "Create portable home folder",
            "Creates a .home folder next to the app for persistent user data"
        )
        self.portable_home_row.setChecked(_settings.get("portable_home", False))
        defaults_layout.addWidget(self.portable_home_row)

        self.portable_config_row = _ToggleRow(
            "Create portable config folder",
            "Creates a .config folder next to the app for persistent configuration"
        )
        self.portable_config_row.setChecked(_settings.get("portable_config", False))
        defaults_layout.addWidget(self.portable_config_row)

        self.auto_scan_row = _ToggleRow(
            "Auto-scan before install",
            "Runs a security scan on every AppImage before installing it"
        )
        self.auto_scan_row.setChecked(_settings.get("auto_scan_before_install", True))
        defaults_layout.addWidget(self.auto_scan_row)

        layout.addWidget(defaults_group)

        shield_group = QGroupBox("Process Isolation")
        shield_layout = QVBoxLayout(shield_group)
        shield_layout.setSpacing(2)

        self.shield_enabled_row = _ToggleRow(
            "Enable process hardening for new installations",
            "Applies rlimits, memory locking, ptrace disable, and malloc hardening"
        )
        self.shield_enabled_row.setChecked(_settings.get("sandbox_default_enabled", True))
        shield_layout.addWidget(self.shield_enabled_row)

        avail = self._detect_sandbox_status()
        if avail:
            status_label = QLabel(
                f"<span style='color:green;'>Available: {avail}</span>"
            )
        else:
            status_label = QLabel(
                "<span style='color:orange;'>Process hardening not available</span>"
            )
        status_label.setWordWrap(True)
        status_label.setStyleSheet("font-size: 11px;")
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
            "enabled and notifies you of available updates"
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
        hooks_label = QLabel(
            f"Hooks directory:<br>"
            f"<code>{hooks_dir}</code>"
        )
        hooks_label.setWordWrap(True)
        hooks_label.setStyleSheet("color: palette(disabled-text); font-size: 12px;")
        hooks_layout.addWidget(hooks_label)

        hooks_desc = QLabel(
            "Place <code>.hook</code> scripts in the hooks directory (or a subdirectory "
            "matching an app name) to run them before the app launches. "
            "Scripts receive <code>APP_NAME</code> and <code>APP_DIR</code> environment variables."
        )
        hooks_desc.setWordWrap(True)
        hooks_desc.setStyleSheet("color: palette(disabled-text); font-size: 11px;")
        hooks_layout.addWidget(hooks_desc)

        open_hooks_btn = QPushButton(get_icon("folder-open"), "Open Hooks Directory")
        open_hooks_btn.clicked.connect(lambda: subprocess.Popen(['xdg-open', hooks_dir], start_new_session=True))
        hooks_layout.addWidget(open_hooks_btn)

        layout.addWidget(hooks_group)

        icon_group = QGroupBox("Icons")
        icon_layout = QVBoxLayout(icon_group)

        self.icon_theme_radio = QRadioButton("Install icon to theme directory (recommended)")
        self.icon_theme_radio.setChecked(_settings.get("icon_in_theme", True))
        self.icon_theme_radio.setToolTip(
            "Copies the icon to ~/.local/share/icons/hicolor/ so all DEs can find it"
        )
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
        self.btn_install_tn.clicked.connect(self._install_thumbnailer)
        tn_btn_row.addWidget(self.btn_install_tn)
        self.btn_remove_tn = QPushButton(get_icon("edit-delete"), "Remove Thumbnailer")
        self.btn_remove_tn.clicked.connect(self._remove_thumbnailer)
        tn_btn_row.addWidget(self.btn_remove_tn)
        tn_btn_row.addStretch()
        tn_layout.addLayout(tn_btn_row)

        tn_info = QLabel(
            "Shows AppImage icons as thumbnails in file managers "
            "(Nautilus, Nemo, Thunar, Dolphin). "
            "Requires tumbler or GNOME thumbnails daemon."
        )
        tn_info.setWordWrap(True)
        tn_info.setStyleSheet("font-size: 11px; color: palette(disabled-text);")
        tn_layout.addWidget(tn_info)

        layout.addWidget(tn_group)

        self._update_thumbnailer_status()

        storage_group = QGroupBox("Storage")
        storage_layout = QVBoxLayout(storage_group)
        storage_layout.setSpacing(4)

        self.remove_source_row = _ToggleRow(
            "Delete source AppImage after successful installation",
            "When enabled, the original .AppImage file is deleted after it is "
            "successfully installed. Helps prevent duplicate files and saves disk space. "
            "Downloaded files from the catalog are always temporary."
        )
        self.remove_source_row.setChecked(_settings.get("auto_remove_source", False))
        storage_layout.addWidget(self.remove_source_row)

        layout.addWidget(storage_group)

        audio_group = QGroupBox("Sound Effects")
        audio_layout = QVBoxLayout(audio_group)
        audio_layout.setSpacing(4)

        self.sound_effects_row = _ToggleRow(
            "Play sound effects",
            "Play sounds for installation, errors, and navigation"
        )
        self.sound_effects_row.setChecked(_settings.get("sound_effects_enabled", True))
        audio_layout.addWidget(self.sound_effects_row)

        layout.addWidget(audio_group)

        help_label = QLabel(
            '<a href="#">Changes take effect on the next install or build.</a>'
        )
        help_label.setStyleSheet("color: palette(disabled-text);")
        help_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(help_label)

        layout.addStretch()

    def _detect_sandbox_status(self) -> str:
        try:
            from niruvi.sandbox import check_shield_available
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
        except Exception:
            return ""

    def _detect_backend_details(self) -> dict:
        try:
            from niruvi.sandbox import check_firejail_available, check_bwrap_available
            return {
                "firejail": check_firejail_available().get("available", False),
                "bwrap": check_bwrap_available().get("available", False),
            }
        except Exception:
            return {"firejail": False, "bwrap": False}

    def _update_thumbnailer_status(self):
        from niruvi.thumbnailer import check_thumbnailer_installed
        if check_thumbnailer_installed():
            self.tn_status_label.setText(
                "<span style='color:green;'>✓ Thumbnailer is installed</span>"
            )
            self.btn_install_tn.setEnabled(False)
            self.btn_remove_tn.setEnabled(True)
        else:
            self.tn_status_label.setText(
                "<span style='color:gray;'>Not installed — AppImages won't show icons in file managers</span>"
            )
            self.btn_install_tn.setEnabled(True)
            self.btn_remove_tn.setEnabled(False)

    def _install_thumbnailer(self):
        from niruvi.thumbnailer import install_thumbnailer
        err = install_thumbnailer()
        if err:
            from niruvi.sound_manager import play as play_sound
            play_sound("error")
            QMessageBox.critical(self, "Install Failed", err)
        else:
            QMessageBox.information(
                self, "Thumbnailer Installed",
                "The AppImage thumbnailer has been installed.\n\n"
                "You may need to restart your file manager "
                "or log out and back in for changes to take effect."
            )
        self._update_thumbnailer_status()

    def _remove_thumbnailer(self):
        from niruvi.thumbnailer import remove_thumbnailer
        err = remove_thumbnailer()
        if err:
            from niruvi.sound_manager import play as play_sound
            play_sound("error")
            QMessageBox.critical(self, "Remove Failed", err)
        else:
            QMessageBox.information(self, "Thumbnailer Removed", "The thumbnailer has been removed.")
        self._update_thumbnailer_status()

    def _browse_install_dir(self):
        dir_path = QFileDialog.getExistingDirectory(
            self, "Select installation directory", self.install_dir_edit.text()
        )
        if dir_path:
            if not _is_local_path(dir_path):
                from niruvi.sound_manager import play as play_sound
                play_sound("warning")
                QMessageBox.warning(
                    self, "Invalid Path",
                    "Cannot use a removable drive or phone path as the install directory.<br><br>"
                    "Please choose a folder on your local filesystem (e.g. <code>~/Applications</code>).",
                )
                return
            self.install_dir_edit.setText(dir_path)

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
        )

    def apply(self) -> bool:
        install_dir = self.install_dir_edit.text()
        if not _is_local_path(install_dir):
            from niruvi.sound_manager import play as play_sound
            play_sound("warning")
            QMessageBox.warning(
                self, "Invalid Path",
                "Cannot set install directory to a removable drive or phone path.<br><br>"
                "Reverting to previous value.",
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
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        if self._page.apply():
            super().accept()

    def reject(self):
        if self._page._has_changes():
            from niruvi.sound_manager import play as play_sound
            play_sound("warning")
            reply = QMessageBox.question(
                self, "Unsaved Changes",
                "You have unsaved changes. Discard them?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        super().reject()
