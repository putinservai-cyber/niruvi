import json
import logging
import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QPushButton, QCheckBox, QDialogButtonBox,
    QFileDialog, QGroupBox, QRadioButton, QLabel, QScrollArea,
    QFrame, QSizePolicy,
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
    "build_output_dir": os.path.expanduser("~/Applications"),
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
        browse_btn = QPushButton(QIcon.fromTheme("folder-open"), "Browse...")
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

        build_group = QGroupBox("AppImage Builder")
        build_layout = QFormLayout(build_group)

        build_dir_layout = QHBoxLayout()
        self.build_output_edit = QLineEdit(
            _settings.get("build_output_dir", os.path.expanduser("~/Applications"))
        )
        self.build_output_edit.setReadOnly(True)
        build_dir_layout.addWidget(self.build_output_edit)
        build_browse_btn = QPushButton(QIcon.fromTheme("folder-open"), "Browse...")
        build_browse_btn.clicked.connect(self._browse_build_output)
        build_dir_layout.addWidget(build_browse_btn)
        build_layout.addRow("Build output directory:", build_dir_layout)

        layout.addWidget(build_group)

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
            self.install_dir_edit.setText(dir_path)

    def _browse_build_output(self):
        dir_path = QFileDialog.getExistingDirectory(
            self, "Select build output directory", self.build_output_edit.text()
        )
        if dir_path:
            self.build_output_edit.setText(dir_path)

    def apply(self):
        _settings["install_dir"] = self.install_dir_edit.text()
        _settings["create_desktop"] = self.create_desktop_row.isChecked()
        _settings["create_shortcut"] = self.shortcut_row.isChecked()
        _settings["portable_home"] = self.portable_home_row.isChecked()
        _settings["portable_config"] = self.portable_config_row.isChecked()
        _settings["icon_in_theme"] = self.icon_theme_radio.isChecked()
        _settings["build_output_dir"] = self.build_output_edit.text()
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
