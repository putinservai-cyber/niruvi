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
from niruvi.utils.sound_manager import play_and
from niruvi.utils.styles import placeholder_style
from niruvi.utils.theme_engine import ThemeMode, get_theme_engine, style


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
        self.preview_btn = QPushButton("\u25b6")
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
        browse_btn.clicked.connect(lambda: play_and("click", self._browse_install_dir))
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

        self.register_mime_row = _ToggleRow(
            "Register as default AppImage handler",
            "Set Niruvi as the default application when you double-click .AppImage files",
        )
        self.register_mime_row.setChecked(_settings.get("register_mime_handler", True))
        defaults_layout.addWidget(self.register_mime_row)

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
            status_label.setStyleSheet("color: #10B981; font-size: 11px;")
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

        self.delta_updates_row = _ToggleRow(
            "Use delta updates (.zsync)",
            "When enabled, Niruvi keeps the installed AppImage and updates "
            "download only the changed blocks instead of the whole file",
        )
        self.delta_updates_row.setChecked(_settings.get("delta_updates", True))
        update_layout.addWidget(self.delta_updates_row)

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

        tray_group = QGroupBox("System Tray")
        tray_layout = QVBoxLayout(tray_group)
        tray_layout.setSpacing(2)

        self.tray_enabled_row = _ToggleRow(
            "Keep tray icon",
            "Show a persistent icon in the system tray so Niruvi can notify you "
            "about updates while the window is closed",
        )
        self.tray_enabled_row.setChecked(_settings.get("tray_enabled", True))
        tray_layout.addWidget(self.tray_enabled_row)

        self.close_to_tray_row = _ToggleRow(
            "Minimize to tray on close",
            "Closing the window hides Niruvi to the tray instead of quitting "
            "(use 'Quit' in the tray menu to exit completely)",
        )
        self.close_to_tray_row.setChecked(_settings.get("close_to_tray", False))
        tray_layout.addWidget(self.close_to_tray_row)

        layout.addWidget(tray_group)

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
        open_hooks_btn.clicked.connect(
            lambda: play_and("click", lambda: subprocess.Popen(["xdg-open", hooks_dir], start_new_session=True))
        )
        hooks_layout.addWidget(open_hooks_btn)

        layout.addWidget(hooks_group)

        plugins_group = QGroupBox("Plugins")
        plugins_layout = QVBoxLayout(plugins_group)
        plugins_layout.setSpacing(4)

        from niruvi.core.plugins import PLUGINS_DIR, ensure_plugins_dir, list_plugins, reload_plugins

        ensure_plugins_dir()
        plugins_label = QLabel(f"Plugins directory:<br><code>{PLUGINS_DIR}</code>")
        plugins_label.setWordWrap(True)
        plugins_label.setStyleSheet(placeholder_style("", 12))
        plugins_layout.addWidget(plugins_label)

        self.plugins_status_label = QLabel()
        plugins_layout.addWidget(self.plugins_status_label)
        self._refresh_plugins_status(list_plugins())

        plugins_row = QHBoxLayout()
        reload_btn = QPushButton(get_icon("view-refresh"), "Reload Plugins")
        reload_btn.clicked.connect(
            lambda: play_and(
                "click",
                lambda: self._refresh_plugins_status(reload_plugins()),
            )
        )
        plugins_row.addWidget(reload_btn)
        open_plugins_btn = QPushButton(get_icon("folder-open"), "Open Plugins Directory")
        open_plugins_btn.clicked.connect(
            lambda: play_and("click", lambda: subprocess.Popen(["xdg-open", PLUGINS_DIR], start_new_session=True))
        )
        plugins_row.addWidget(open_plugins_btn)
        plugins_row.addStretch()
        plugins_layout.addLayout(plugins_row)

        plugins_desc = QLabel(
            "Drop Python modules here to extend Niruvi. A plugin declares a "
            "<code>handlers</code> dict or a <code>register(api)</code> function "
            "for events such as <code>app_installed</code>, <code>app_launched</code>, "
            "<code>app_updated</code>, <code>app_removed</code>, <code>update_available</code>, "
            "<code>scan_completed</code> and <code>settings_changed</code>."
        )
        plugins_desc.setWordWrap(True)
        plugins_desc.setStyleSheet(placeholder_style("", 11))
        plugins_layout.addWidget(plugins_desc)

        layout.addWidget(plugins_group)

        store_group = QGroupBox("App Store")
        store_layout = QVBoxLayout(store_group)
        store_layout.setSpacing(4)

        store_desc = QLabel(
            "Catalog URL for the App Store. The index must be a JSON document: "
            'a list of entries or {"apps": [...]}, each with a <code>name</code> '
            "and <code>url</code> (plus optional description, version, icon, "
            "arch, categories). Downloads are cached for offline use."
        )
        store_desc.setWordWrap(True)
        store_desc.setStyleSheet(placeholder_style("", 11))
        store_layout.addWidget(store_desc)

        url_row = QHBoxLayout()
        url_row.addWidget(QLabel("Index URL:"))
        self.store_url_edit = QLineEdit(_settings.get("store_index_url", ""))
        self.store_url_edit.setPlaceholderText("https://example.org/niruvi-catalog.json")
        url_row.addWidget(self.store_url_edit, 1)
        store_layout.addLayout(url_row)

        layout.addWidget(store_group)

        vt_group = QGroupBox("VirusTotal")
        vt_layout = QVBoxLayout(vt_group)
        vt_layout.setSpacing(4)

        vt_desc = QLabel(
            "Optional: scan AppImages with VirusTotal before installing. "
            "Files are uploaded to virustotal.com; only enable this if you "
            "accept that. Get a free API key at "
            "<a href='https://www.virustotal.com/'>virustotal.com</a>."
        )
        vt_desc.setWordWrap(True)
        vt_desc.setOpenExternalLinks(True)
        vt_desc.setStyleSheet(placeholder_style("", 11))
        vt_layout.addWidget(vt_desc)

        key_row = QHBoxLayout()
        key_row.addWidget(QLabel("API key:"))
        self.vt_key_edit = QLineEdit(_settings.get("virustotal_api_key", ""))
        self.vt_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.vt_key_edit.setPlaceholderText("paste your API key here")
        key_row.addWidget(self.vt_key_edit, 1)
        vt_layout.addLayout(key_row)

        vt_btn_row = QHBoxLayout()
        self.vt_test_btn = QPushButton(get_icon("emblem-ok", "dialog-information"), "Test Key")
        self.vt_test_btn.clicked.connect(self._test_vt_key)
        vt_btn_row.addWidget(self.vt_test_btn)
        self.vt_test_label = QLabel()
        vt_btn_row.addWidget(self.vt_test_label)
        vt_btn_row.addStretch()
        vt_layout.addLayout(vt_btn_row)

        layout.addWidget(vt_group)

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
        self.btn_install_tn.clicked.connect(lambda: play_and("click", self._install_thumbnailer))
        tn_btn_row.addWidget(self.btn_install_tn)
        self.btn_remove_tn = QPushButton(get_icon("edit-delete"), "Remove Thumbnailer")
        self.btn_remove_tn.clicked.connect(lambda: play_and("click", self._remove_thumbnailer))
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
            "Feedback sounds",
            "feedback",
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
            "Navigation sounds",
            "navigation",
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
            "Notification sounds",
            "notifications",
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
            "When enabled, Niruvi will periodically check GitHub for new versions. No personal data is transmitted.",
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
            self.tn_status_label.setStyleSheet("color: #10B981;")
            self.btn_install_tn.setEnabled(False)
            self.btn_remove_tn.setEnabled(True)
        else:
            self.tn_status_label.setText("Not installed — AppImages won't show icons in file managers")
            self.tn_status_label.setStyleSheet(style("color: {colors.subtle};"))
            self.btn_install_tn.setEnabled(True)
            self.btn_remove_tn.setEnabled(False)

    def _refresh_plugins_status(self, names: list[str]):
        if names:
            self.plugins_status_label.setText(f"Active plugins: {', '.join(names)}")
        else:
            self.plugins_status_label.setText("No plugins loaded.")

    def _test_vt_key(self):
        from niruvi.app.virustotal import test_key

        key = self.vt_key_edit.text().strip()
        if not key:
            self.vt_test_label.setText("Enter a key first.")
            return
        self.vt_test_btn.setEnabled(False)
        self.vt_test_label.setText("Testing...")
        play_sound("click")
        from PyQt6.QtCore import QThread, pyqtSignal

        class _KeyTest(QThread):
            done = pyqtSignal(bool, str)

            def run(self):
                try:
                    ok = test_key(key)
                    self.done.emit(ok, "")
                except Exception as e:
                    self.done.emit(False, str(e))

        self._vt_test_thread = _KeyTest(self)
        self._vt_test_thread.done.connect(self._on_vt_test_done)
        self._vt_test_thread.finished.connect(self._vt_test_thread.deleteLater)
        self._vt_test_thread.start()

    def _on_vt_test_done(self, ok: bool, err: str):
        self.vt_test_btn.setEnabled(True)
        if ok:
            self.vt_test_label.setText("Key is valid.")
        else:
            self.vt_test_label.setText(f"Invalid key ({err}).")

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
        mode_map = {"auto": ThemeMode.AUTO, "light": ThemeMode.LIGHT, "dark": ThemeMode.DARK}
        mode = mode_map.get(self.theme_combo.currentData(), ThemeMode.AUTO)
        get_theme_engine().mode = mode

    def _has_changes(self) -> bool:
        return bool(
            self.install_dir_edit.text() != _settings.get("install_dir", DEFAULT_INSTALL_DIR)
            or self.create_desktop_row.isChecked() != _settings.get("create_desktop", True)
            or self.shortcut_row.isChecked() != _settings.get("create_shortcut", False)
            or self.portable_home_row.isChecked() != _settings.get("portable_home", False)
            or self.portable_config_row.isChecked() != _settings.get("portable_config", False)
            or self.auto_scan_row.isChecked() != _settings.get("auto_scan_before_install", True)
            or self.register_mime_row.isChecked() != _settings.get("register_mime_handler", True)
            or self.auto_update_apps_row.isChecked() != _settings.get("auto_update_apps", False)
            or self.delta_updates_row.isChecked() != _settings.get("delta_updates", True)
            or self.tray_enabled_row.isChecked() != _settings.get("tray_enabled", True)
            or self.close_to_tray_row.isChecked() != _settings.get("close_to_tray", False)
            or self.update_interval_combo.currentText() != _settings.get("update_check_interval", "weekly")
            or self.icon_theme_radio.isChecked() != _settings.get("icon_in_theme", True)
            or self.shield_enabled_row.isChecked() != _settings.get("sandbox_default_enabled", True)
            or self.backend_combo.currentData() != _settings.get("sandbox_default_backend", "shield")
            or self.remove_source_row.isChecked() != _settings.get("auto_remove_source", False)
            or self.sound_effects_row.isChecked() != _settings.get("sound_effects_enabled", True)
            or abs(self.volume_slider.value() / 100.0 - _settings.get("sound_volume", 0.7)) > 0.001
            or self.sound_feedback_row.isChecked() != _settings.get("sound_feedback_enabled", True)
            or self.sound_navigation_row.isChecked() != _settings.get("sound_navigation_enabled", True)
            or self.sound_notifications_row.isChecked() != _settings.get("sound_notifications_enabled", True)
            or abs(self.feedback_volume_slider.value() / 100.0 - _settings.get("sound_volume_feedback", 1.0)) > 0.001
            or abs(self.nav_volume_slider.value() / 100.0 - _settings.get("sound_volume_navigation", 1.0)) > 0.001
            or abs(self.notif_volume_slider.value() / 100.0 - _settings.get("sound_volume_notifications", 1.0)) > 0.001
            or self.privacy_updates_row.isChecked() != _settings.get("privacy_allow_update_checks", True)
            or self.theme_combo.currentData() != _settings.get("theme_mode", "auto")
            or self.vt_key_edit.text().strip() != _settings.get("virustotal_api_key", "").strip()
            or self.store_url_edit.text().strip() != _settings.get("store_index_url", "").strip()
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
        changed = self._changed_keys()
        _settings["install_dir"] = install_dir
        _settings["create_desktop"] = self.create_desktop_row.isChecked()
        _settings["create_shortcut"] = self.shortcut_row.isChecked()
        _settings["portable_home"] = self.portable_home_row.isChecked()
        _settings["portable_config"] = self.portable_config_row.isChecked()
        _settings["auto_scan_before_install"] = self.auto_scan_row.isChecked()
        _settings["register_mime_handler"] = self.register_mime_row.isChecked()
        _settings["auto_update_apps"] = self.auto_update_apps_row.isChecked()
        _settings["delta_updates"] = self.delta_updates_row.isChecked()
        _settings["tray_enabled"] = self.tray_enabled_row.isChecked()
        _settings["close_to_tray"] = self.close_to_tray_row.isChecked()
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
        _settings["virustotal_api_key"] = self.vt_key_edit.text().strip()
        _settings["store_index_url"] = self.store_url_edit.text().strip()
        save_settings()
        from niruvi.core.plugins import emit

        emit("settings_changed", keys=changed)
        return True

    def _changed_keys(self) -> list:
        changed = []
        pairs = [
            ("install_dir", self.install_dir_edit.text(), _settings.get("install_dir", DEFAULT_INSTALL_DIR)),
            ("create_desktop", self.create_desktop_row.isChecked(), _settings.get("create_desktop", True)),
            ("create_shortcut", self.shortcut_row.isChecked(), _settings.get("create_shortcut", False)),
            ("portable_home", self.portable_home_row.isChecked(), _settings.get("portable_home", False)),
            ("portable_config", self.portable_config_row.isChecked(), _settings.get("portable_config", False)),
            (
                "auto_scan_before_install",
                self.auto_scan_row.isChecked(),
                _settings.get("auto_scan_before_install", True),
            ),
            (
                "register_mime_handler",
                self.register_mime_row.isChecked(),
                _settings.get("register_mime_handler", True),
            ),
            ("auto_update_apps", self.auto_update_apps_row.isChecked(), _settings.get("auto_update_apps", False)),
            ("delta_updates", self.delta_updates_row.isChecked(), _settings.get("delta_updates", True)),
            ("tray_enabled", self.tray_enabled_row.isChecked(), _settings.get("tray_enabled", True)),
            ("close_to_tray", self.close_to_tray_row.isChecked(), _settings.get("close_to_tray", False)),
            (
                "update_check_interval",
                self.update_interval_combo.currentText(),
                _settings.get("update_check_interval", "weekly"),
            ),
            ("icon_in_theme", self.icon_theme_radio.isChecked(), _settings.get("icon_in_theme", True)),
            (
                "sandbox_default_enabled",
                self.shield_enabled_row.isChecked(),
                _settings.get("sandbox_default_enabled", True),
            ),
            (
                "sandbox_default_backend",
                self.backend_combo.currentData(),
                _settings.get("sandbox_default_backend", "shield"),
            ),
            ("auto_remove_source", self.remove_source_row.isChecked(), _settings.get("auto_remove_source", False)),
            ("sound_effects_enabled", self.sound_effects_row.isChecked(), _settings.get("sound_effects_enabled", True)),
            (
                "sound_feedback_enabled",
                self.sound_feedback_row.isChecked(),
                _settings.get("sound_feedback_enabled", True),
            ),
            (
                "sound_navigation_enabled",
                self.sound_navigation_row.isChecked(),
                _settings.get("sound_navigation_enabled", True),
            ),
            (
                "sound_notifications_enabled",
                self.sound_notifications_row.isChecked(),
                _settings.get("sound_notifications_enabled", True),
            ),
            (
                "privacy_allow_update_checks",
                self.privacy_updates_row.isChecked(),
                _settings.get("privacy_allow_update_checks", True),
            ),
            ("theme_mode", self.theme_combo.currentData(), _settings.get("theme_mode", "auto")),
            ("virustotal_api_key", self.vt_key_edit.text().strip(), _settings.get("virustotal_api_key", "").strip()),
            ("store_index_url", self.store_url_edit.text().strip(), _settings.get("store_index_url", "").strip()),
        ]
        for key, new, old in pairs:
            if new != old:
                changed.append(key)
        return changed


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
