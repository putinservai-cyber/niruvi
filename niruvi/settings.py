import json
import logging
import os

from PyQt6.QtCore import Qt
from niruvi.utils import get_icon
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QPushButton, QDialogButtonBox,
    QFileDialog, QGroupBox, QRadioButton, QLabel, QScrollArea,
    QFrame, QSizePolicy, QMessageBox,
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

        help_label = QLabel(
            '<a href="#" style="color: #888;">Changes take effect on the next install or build.</a>'
        )
        help_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(help_label)

        layout.addStretch()

    def _browse_install_dir(self):
        dir_path = QFileDialog.getExistingDirectory(
            self, "Select installation directory", self.install_dir_edit.text()
        )
        if dir_path:
            if not _is_local_path(dir_path):
                QMessageBox.warning(
                    self, "Invalid Path",
                    "Cannot use a removable drive or phone path as the install directory.<br><br>"
                    "Please choose a folder on your local filesystem (e.g. <code>~/Applications</code>).",
                )
                return
            self.install_dir_edit.setText(dir_path)

    def apply(self):
        install_dir = self.install_dir_edit.text()
        if not _is_local_path(install_dir):
            QMessageBox.warning(
                self, "Invalid Path",
                "Cannot set install directory to a removable drive or phone path.<br><br>"
                "Reverting to previous value.",
            )
            self.install_dir_edit.setText(_settings.get("install_dir", DEFAULT_INSTALL_DIR))
            return
        _settings["install_dir"] = install_dir
        _settings["create_desktop"] = self.create_desktop_row.isChecked()
        _settings["create_shortcut"] = self.shortcut_row.isChecked()
        _settings["portable_home"] = self.portable_home_row.isChecked()
        _settings["portable_config"] = self.portable_config_row.isChecked()
        _settings["auto_scan_before_install"] = self.auto_scan_row.isChecked()
        _settings["icon_in_theme"] = self.icon_theme_radio.isChecked()
        save_settings()


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
        self._page.apply()
        super().accept()
