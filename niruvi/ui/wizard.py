"""Installer Wizard — Windows-style multi-page AppImage installer.

Pages: Welcome → License → InstallType → Destination (Custom only)
       → Components → Progress → Finish
"""

import json
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal

logger = logging.getLogger(__name__)


def sanitize_install_name(name: str) -> str:
    """Replace spaces and unsafe characters with hyphens for JuNest/bwrap compatibility.

    JuNest and bubblewrap cannot handle spaces in install paths. This function
    replaces them so the directory name is safe while preserving readability.
    """
    # Replace any character that isn't a word char, hyphen, or period with a hyphen
    sanitized = re.sub(r"[^\w\-.]", "-", name)
    # Collapse multiple hyphens into one
    sanitized = re.sub(r"-+", "-", sanitized)
    # Strip leading/trailing hyphens
    sanitized = sanitized.strip("-")
    # Prevent empty result
    return sanitized or "app"


logger = logging.getLogger(__name__)
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QTextEdit,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from niruvi.core.worker import ExtractionWorker, _ensure_local, _is_removable_path, start_worker
from niruvi.desktop.appimage_assets import extract_metadata
from niruvi.desktop.appimage_metadata import AppImageMetadata
from niruvi.desktop.desktop_utils import (
    create_desktop_entry,
    create_desktop_shortcut,
    find_icon_in_appdir,
    get_version,
    parse_desktop_file_content,
    refresh_desktop_database,
)
from niruvi.desktop.icon_utils import get_pixmap_from_file
from niruvi.desktop.installation_registry import InstallationRecord, InstallationRegistry
from niruvi.ui.settings import get_settings
from niruvi.utils import get_icon
from niruvi.utils.sound_manager import play as play_sound

_PREDEFINED_LOCATIONS = [
    ("~/Applications (Recommended)", os.path.expanduser("~/Applications")),
    ("~/AppImages", os.path.expanduser("~/AppImages")),
    ("~/Niruvi", os.path.expanduser("~/Niruvi")),
]


class WelcomePage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Welcome")
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(80, 80)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.icon_label)

        self.app_name_label = QLabel()
        self.app_name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = self.app_name_label.font()
        f.setPointSize(18)
        f.setBold(True)
        self.app_name_label.setFont(f)
        layout.addWidget(self.app_name_label)

        self.desc_label = QLabel()
        self.desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.desc_label.setWordWrap(True)
        layout.addWidget(self.desc_label)

        layout.addSpacing(8)

        self.info_label = QLabel()
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        layout.addStretch()

        hint = QLabel("This wizard will install the AppImage to your system.\nClick Next to continue.")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: palette(placeholderText); font-size: 11px;")
        layout.addWidget(hint)

    def set_app_info(self, name: str, desc: str, icon_pixmap, size_mb: float, arch: str):
        self.app_name_label.setText(name)
        if desc:
            self.desc_label.setText(desc)
        if icon_pixmap and not icon_pixmap.isNull():
            self.icon_label.setPixmap(icon_pixmap)
        else:
            generic = get_icon("package-x-generic", "application-x-archive").pixmap(80, 80)
            if generic and not generic.isNull():
                self.icon_label.setPixmap(generic)
        info_parts = [f"Size: {size_mb:.1f} MB"]
        if arch:
            info_parts.append(f"Architecture: {arch}")
        self.info_label.setText(" | ".join(info_parts))


class LicensePage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("License Agreement")
        self.setSubTitle("Please review the license terms before installing.")
        layout = QVBoxLayout(self)

        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setStyleSheet("background: palette(base); border: 1px solid palette(mid); border-radius: 4px;")
        layout.addWidget(self.text_edit)

        self.accept_check = QCheckBox("I accept the terms of the license agreement")
        self.accept_check.toggled.connect(lambda: self.completeChanged.emit())
        layout.addWidget(self.accept_check)

    def isComplete(self):
        return self.accept_check.isChecked()

    def set_license_text(self, text: str):
        self.text_edit.setPlainText(text)


class InstallTypePage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Installation Type")
        self.setSubTitle("Choose how to install this application.")
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        self.type_group = QButtonGroup(self)

        rec_layout = QVBoxLayout()
        self.rec_radio = QRadioButton("Recommended")
        self.rec_radio.setChecked(True)
        self.type_group.addButton(self.rec_radio)
        rec_layout.addWidget(self.rec_radio)
        rec_desc = QLabel("Install to the default location with standard settings. Recommended for most users.")
        rec_desc.setWordWrap(True)
        rec_desc.setStyleSheet("color: palette(placeholderText); font-size: 11px; padding-left: 24px;")
        rec_layout.addWidget(rec_desc)
        layout.addLayout(rec_layout)

        cust_layout = QVBoxLayout()
        self.cust_radio = QRadioButton("Custom")
        self.type_group.addButton(self.cust_radio)
        cust_layout.addWidget(self.cust_radio)
        cust_desc = QLabel("Choose a custom installation location and configure advanced options.")
        cust_desc.setWordWrap(True)
        cust_desc.setStyleSheet("color: palette(placeholderText); font-size: 11px; padding-left: 24px;")
        cust_layout.addWidget(cust_desc)
        layout.addLayout(cust_layout)

        layout.addStretch()

    def is_custom(self) -> bool:
        return self.cust_radio.isChecked()


class DestinationPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Destination Folder")
        self.setSubTitle("Choose where to install the application.")
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self.loc_group = QButtonGroup(self)
        self._loc_buttons = []
        self._loc_paths = []

        loc_title = QLabel("<b>Install to:</b>")
        layout.addWidget(loc_title)

        for label, path in _PREDEFINED_LOCATIONS:
            rb = QRadioButton(label)
            rb.setChecked(False)
            self.loc_group.addButton(rb)
            self._loc_buttons.append(rb)
            self._loc_paths.append(path)
            layout.addWidget(rb)

        self.custom_radio = QRadioButton("Custom...")
        self.loc_group.addButton(self.custom_radio)
        layout.addWidget(self.custom_radio)

        self._loc_buttons[0].setChecked(True)

        layout.addSpacing(8)

        path_row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        path_row.addWidget(self.path_edit, 1)
        self.browse_btn = QPushButton(get_icon("folder-open"), "Browse...")
        self.browse_btn.clicked.connect(self._on_browse)
        path_row.addWidget(self.browse_btn)
        layout.addLayout(path_row)

        self.loc_group.buttonToggled.connect(self._on_selection_changed)
        self._on_selection_changed()

        layout.addStretch()

        space_label = QLabel("Required space: <b>--</b> &nbsp;|&nbsp; Available: <b>--</b>")
        space_label.setStyleSheet("color: palette(placeholderText); font-size: 11px;")
        layout.addWidget(space_label)
        self.space_label = space_label

    def _on_selection_changed(self):
        for i, rb in enumerate(self._loc_buttons):
            if rb.isChecked():
                self.path_edit.setText(self._loc_paths[i])
                return
        if self.custom_radio.isChecked():
            pass

    def set_app_name(self, name: str):
        self._app_name = name
        paths = []
        for _label, base in _PREDEFINED_LOCATIONS:
            paths.append(os.path.join(base, name))
        self._loc_paths = paths
        self._on_selection_changed()

    def _on_browse(self):
        current = self.path_edit.text() or os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(self, "Select Install Location", current)
        if path:
            self.path_edit.setText(os.path.join(path, getattr(self, "_app_name", "app")))
            self.custom_radio.setChecked(True)

    def get_destination(self, default_base: str) -> str:
        if self.custom_radio.isChecked():
            return self.path_edit.text()
        for i, rb in enumerate(self._loc_buttons):
            if rb.isChecked():
                return self._loc_paths[i]
        return os.path.join(default_base, getattr(self, "_app_name", "app"))

    def set_space_info(self, size_mb: float):
        import os
        import shutil

        dest = self.path_edit.text() or os.path.expanduser("~")
        # Walk up to the nearest existing directory so disk_usage doesn't fail
        # on paths that don't exist yet (e.g. a new folder with spaces)
        check_path = Path(dest)
        while check_path and not check_path.exists():
            check_path = check_path.parent
        if not check_path or not check_path.exists():
            logger.warning("No valid path found for disk usage check: %s", dest)
            self.space_label.setText(f"Required space: <b>{size_mb:.0f} MB</b> &nbsp;|&nbsp; Available: unavailable")
            return
        try:
            usage = shutil.disk_usage(str(check_path))
            avail_gb = usage.free / (1024**3)
            self.space_label.setText(
                f"Required space: <b>{size_mb:.0f} MB</b> &nbsp;|&nbsp; Available: <b>{avail_gb:.1f} GB</b>"
            )
        except OSError as e:
            logger.debug("Failed to get disk usage info: %s", e)
            self.space_label.setText(f"Required space: <b>{size_mb:.0f} MB</b> &nbsp;|&nbsp; Available: unavailable")


class ComponentsPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Components")
        self.setSubTitle("Choose additional components to install.")
        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        sec_title = QLabel("<b>Desktop Integration</b>")
        layout.addWidget(sec_title)

        self.cb_desktop_file = QCheckBox("Add to Applications Menu")
        self.cb_desktop_file.setChecked(get_settings().get("create_desktop", True))
        self.cb_desktop_file.setToolTip(
            "Creates a .desktop file so the app appears in your desktop environment's application launcher."
        )
        layout.addWidget(self.cb_desktop_file)

        self.cb_desktop_shortcut = QCheckBox("Create Desktop Shortcut")
        self.cb_desktop_shortcut.setChecked(get_settings().get("create_shortcut", False))
        self.cb_desktop_shortcut.setToolTip("Places a shortcut icon on your Desktop.")
        layout.addWidget(self.cb_desktop_shortcut)

        self.cb_file_assoc = QCheckBox("Register File Associations")
        self.cb_file_assoc.setChecked(True)
        self.cb_file_assoc.setToolTip("Associate file types with this application.")
        layout.addWidget(self.cb_file_assoc)

        layout.addSpacing(12)
        data_title = QLabel("<b>Data Isolation</b>")
        layout.addWidget(data_title)

        self.cb_portable_home = QCheckBox("Portable Home Folder (.home)")
        self.cb_portable_home.setChecked(get_settings().get("portable_home", False))
        layout.addWidget(self.cb_portable_home)

        self.cb_portable_config = QCheckBox("Portable Config Folder (.config)")
        self.cb_portable_config.setChecked(get_settings().get("portable_config", False))
        layout.addWidget(self.cb_portable_config)

        layout.addSpacing(12)
        sec_title = QLabel("<b>Process Hardening</b>")
        layout.addWidget(sec_title)

        self.cb_hardening = QCheckBox("Enable Memory & Process Hardening")
        self.cb_hardening.setChecked(get_settings().get("sandbox_default_enabled", True))
        layout.addWidget(self.cb_hardening)

        layout.addSpacing(12)
        from niruvi.app.virustotal import get_api_key

        self.cb_vt_scan: QCheckBox | None = None
        if get_api_key():
            sec_title = QLabel("<b>Security Scan</b>")
            layout.addWidget(sec_title)
            self.cb_vt_scan = QCheckBox("Scan with VirusTotal before installing")
            self.cb_vt_scan.setChecked(True)
            self.cb_vt_scan.setToolTip(
                "Uploads the AppImage to VirusTotal for malware analysis. "
                "Installation waits for the report and warns you if anything is flagged."
            )
            layout.addWidget(self.cb_vt_scan)
        else:
            self.cb_vt_scan = None

        layout.addStretch()

    def validate(self) -> bool:
        if not self.cb_desktop_file.isChecked() and not self.cb_desktop_shortcut.isChecked():
            play_sound("warning")
            reply = QMessageBox.question(
                self,
                "No Integration",
                "No integration options selected. Proceed anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            return reply == QMessageBox.StandardButton.Yes
        return True


class ProgressPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Installing")
        self.setSubTitle("Please wait while the application is installed.")
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        self.task_label = QLabel("Preparing...")
        self.task_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = self.task_label.font()
        f.setPointSize(14)
        f.setBold(True)
        self.task_label.setFont(f)
        layout.addWidget(self.task_label)

        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: palette(placeholderText);")
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFixedHeight(24)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid palette(mid);
                border-radius: 2px;
                text-align: center;
                background-color: palette(base);
            }
            QProgressBar::chunk {
                background-color: palette(highlight);
                border-radius: 1px;
            }
        """)
        layout.addWidget(self.progress_bar)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(140)
        self.log_text.setVisible(False)
        mono = QFont()
        mono.setFamily("monospace")
        mono.setStyleHint(QFont.StyleHint.TypeWriter)
        mono.setPointSize(9)
        self.log_text.setFont(mono)
        layout.addWidget(self.log_text)

        self.btn_toggle = QPushButton(get_icon("format-justify-left"), "Show Details")
        self.btn_toggle.setCheckable(True)
        self.btn_toggle.toggled.connect(self._on_toggle_details)
        layout.addWidget(self.btn_toggle)

    def set_task(self, task: str, status: str = ""):
        self.task_label.setText(task)
        self.status_label.setText(status)

    def set_progress(self, value: int):
        self.progress_bar.setValue(value)

    def append_log(self, msg: str):
        self.log_text.append(msg)

    def _on_toggle_details(self, visible: bool) -> None:
        self.log_text.setVisible(visible)
        self.btn_toggle.setText("Hide Details" if visible else "Show Details")
        sb = self.log_text.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())


class FinishPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Installation Complete")
        self.setFinalPage(True)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        self.icon_label = QLabel()
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setFixedSize(80, 80)
        layout.addWidget(self.icon_label)

        self.title_label = QLabel()
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = self.title_label.font()
        f.setPointSize(16)
        f.setBold(True)
        self.title_label.setFont(f)
        layout.addWidget(self.title_label)

        self.detail_label = QLabel()
        self.detail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label)

        layout.addSpacing(12)

        self.launch_check = QCheckBox("Launch application now")
        self.launch_check.setChecked(True)
        layout.addWidget(self.launch_check)

        layout.addStretch()

        hint = QLabel("The application has been installed and is ready to use.")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: palette(placeholderText); font-size: 11px;")
        layout.addWidget(hint)

    def set_completed(self, app_name: str, icon_pixmap, detail: str = ""):
        if icon_pixmap and not icon_pixmap.isNull():
            self.icon_label.setPixmap(icon_pixmap)
        else:
            generic = get_icon("dialog-ok").pixmap(80, 80)
            if generic and not generic.isNull():
                self.icon_label.setPixmap(generic)
        self.title_label.setText(f"{app_name} was installed successfully!")
        self.detail_label.setText(detail)

    def should_launch(self) -> bool:
        return self.launch_check.isChecked()


class InstallWizard(QWizard):

    def _wbutton(self, button: QWizard.WizardButton) -> QAbstractButton:
        btn = self.button(button)
        assert btn is not None
        return btn

    def __init__(self, appimage_path=None, parent=None, appimage_info=None, icon_data=None):
        super().__init__(parent)
        self.setMinimumSize(560, 480)
        self.resize(620, 540)
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setStyleSheet("""
            QWizardPage { background-color: palette(window); }
            QProgressBar {
                border: 1px solid palette(mid);
                border-radius: 2px;
                text-align: center;
                height: 6px;
                background-color: palette(base);
            }
            QProgressBar::chunk {
                background-color: palette(highlight);
                border-radius: 1px;
            }
            QPushButton {
                padding: 6px 16px;
                border: 1px solid palette(mid);
                border-radius: 2px;
                background-color: palette(button);
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: palette(light);
                border-color: palette(highlight);
            }
            QPushButton:pressed {
                background-color: palette(midlight);
            }
            QRadioButton {
                spacing: 6px;
                font-size: 12px;
            }
            QRadioButton::indicator {
                width: 18px;
                height: 18px;
                border-radius: 9px;
                border: 2px solid palette(mid);
            }
            QRadioButton::indicator:checked {
                background-color: palette(highlight);
                border-color: palette(highlight);
            }
            QCheckBox {
                spacing: 8px;
                font-size: 12px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 1px solid palette(mid);
            }
            QCheckBox::indicator:checked {
                background-color: palette(highlight);
                border-color: palette(highlight);
            }
        """)

        self.appimage_path: str | None = appimage_path
        self.dest_dir: str | None = None
        self.app_name: str | None = None
        self.worker: ExtractionWorker | None = None
        self._extraction_started = False
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(150)
        self._progress_timer.timeout.connect(self._on_progress_tick)
        self._progress_tick = 0
        self._progress_real = -1

        self._appimage_info = appimage_info or {}
        self._icon_data = icon_data
        self._icon_pixmap = None
        self._desktop_info = None
        self._backup_dir: str | None = None
        self._architecture = ""
        self._size_mb = 0.0
        self._license_text: str | None = None
        self._scan_result = None

        self._welcome_page: WelcomePage = None  # type: ignore[assignment]
        self._license_page: LicensePage = None  # type: ignore[assignment]
        self._installtype_page: InstallTypePage = None  # type: ignore[assignment]
        self._destination_page: DestinationPage = None  # type: ignore[assignment]
        self._components_page: ComponentsPage = None  # type: ignore[assignment]
        self._progress_page: ProgressPage = None  # type: ignore[assignment]
        self._finish_page: FinishPage = None  # type: ignore[assignment]

        self._page_ids: dict[str, int] = {}

        self._build_pages()
        self._configure_buttons()

        if appimage_path:
            self._select_file(appimage_path)

        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowMaximizeButtonHint)
        self.setFixedSize(620, 540)
        self.currentIdChanged.connect(self._on_page_changed)

    def _build_pages(self):
        self.setWindowTitle("Install AppImage")

        self._welcome_page = WelcomePage(self)
        pid = self.addPage(self._welcome_page)
        self._page_ids["welcome"] = pid

        self._license_page = LicensePage(self)
        pid = self.addPage(self._license_page)
        self._page_ids["license"] = pid

        self._installtype_page = InstallTypePage(self)
        pid = self.addPage(self._installtype_page)
        self._page_ids["installtype"] = pid

        self._destination_page = DestinationPage(self)
        pid = self.addPage(self._destination_page)
        self._page_ids["destination"] = pid

        self._components_page = ComponentsPage(self)
        pid = self.addPage(self._components_page)
        self._page_ids["components"] = pid

        self._progress_page = ProgressPage(self)
        pid = self.addPage(self._progress_page)
        self._page_ids["progress"] = pid

        self._finish_page = FinishPage(self)
        pid = self.addPage(self._finish_page)
        self._page_ids["finish"] = pid

        self.currentIdChanged.connect(lambda _: play_sound("navigation"))

    def _configure_buttons(self):
        self.setButtonText(QWizard.WizardButton.CancelButton, "Cancel")
        self._wbutton(QWizard.WizardButton.CancelButton).setIcon(get_icon("dialog-cancel"))
        self.setButtonText(QWizard.WizardButton.BackButton, "Back")
        self._wbutton(QWizard.WizardButton.BackButton).setIcon(get_icon("go-previous"))
        self.setButtonText(QWizard.WizardButton.NextButton, "Next")
        self._wbutton(QWizard.WizardButton.NextButton).setIcon(get_icon("go-next"))
        self.setButtonText(QWizard.WizardButton.FinishButton, "Finish")
        self._wbutton(QWizard.WizardButton.FinishButton).setIcon(get_icon("dialog-ok"))
        self._wbutton(QWizard.WizardButton.FinishButton).setEnabled(False)

    def _select_file(self, path: str):
        if _is_removable_path(path):
            self.appimage_path = _ensure_local(path, self._log)
        else:
            self.appimage_path = path
        base = os.path.splitext(os.path.basename(self.appimage_path))[0]
        self.app_name = base

        self._size_mb = os.path.getsize(self.appimage_path) / (1024 * 1024)
        name = self._appimage_info.get("Name", base)
        desc = self._appimage_info.get("Comment", "")
        self.app_name = name

        if self._icon_data:
            from niruvi.desktop.icon_utils import get_pixmap_from_data

            pixmap = get_pixmap_from_data(self._icon_data, 80)
            if pixmap and not pixmap.isNull():
                self._icon_pixmap = pixmap

        if not self._icon_pixmap:
            self._load_icon_and_metadata()

        self._welcome_page.set_app_info(name, desc, self._icon_pixmap, self._size_mb, self._architecture)

        dest_base = get_settings()["install_dir"]
        self.dest_dir = os.path.join(dest_base, self.app_name)
        self._destination_page.set_app_name(self.app_name)
        self._destination_page.set_space_info(self._size_mb)

    def _log(self, msg: str):
        if self._progress_page:
            self._progress_page.append_log(msg)

    def _load_icon_and_metadata(self):
        try:
            meta = AppImageMetadata(self.appimage_path)
            self._architecture = meta.architecture
        except Exception as e:
            logger.debug("Failed to load AppImage metadata: %s", e, exc_info=True)
        if self._icon_pixmap:
            return
        try:
            with tempfile.TemporaryDirectory(prefix="aim-info-") as tmp:
                assets = extract_metadata(self.appimage_path, tmp)
                icon_path = assets.get("icon")
                if icon_path and os.path.isfile(icon_path):
                    pixmap = get_pixmap_from_file(icon_path, 80)
                    if pixmap and not pixmap.isNull():
                        self._icon_pixmap = pixmap
                        self._welcome_page.icon_label.setPixmap(pixmap)
                desktop_path = assets.get("desktop")
                if desktop_path:
                    content = Path(desktop_path).read_text(encoding="utf-8", errors="ignore")
                    self._desktop_info = parse_desktop_file_content(content)
                    name = self._desktop_info.get("Name", self.app_name)
                    self.app_name = name
                    self._welcome_page.app_name_label.setText(name)
                    comment = self._desktop_info.get("Comment", "")
                    if comment:
                        self._welcome_page.desc_label.setText(comment)
                license_path = assets.get("license")
                if license_path and os.path.isfile(license_path):
                    self._license_text = Path(license_path).read_text(encoding="utf-8", errors="ignore")
                    self._license_page.set_license_text(self._license_text)
        except Exception as e:
            logger.debug("Failed to extract icon and metadata: %s", e, exc_info=True)

    def set_appimage(self, path: str):
        self._select_file(path)

    def _on_page_changed(self, idx):
        if idx == self._page_ids.get("progress"):
            self._do_install()
            self._wbutton(QWizard.WizardButton.FinishButton).hide()
        elif (page := self.currentPage()) is not None and page.isFinalPage():
            self._wbutton(QWizard.WizardButton.BackButton).hide()

    def nextId(self):
        cid = self.currentId()
        if cid == self._page_ids.get("welcome"):
            if self._license_text:
                return self._page_ids["license"]
            return self._page_ids["installtype"]
        if cid == self._page_ids.get("license"):
            return self._page_ids["installtype"]
        if cid == self._page_ids.get("installtype"):
            if self._installtype_page and self._installtype_page.is_custom():
                return self._page_ids["destination"]
            return self._page_ids["components"]
        if cid == self._page_ids.get("destination"):
            return self._page_ids["components"]
        if cid == self._page_ids.get("components"):
            return self._page_ids["progress"]
        if cid == self._page_ids.get("progress"):
            return self._page_ids["finish"]
        return -1

    def validateCurrentPage(self):
        cid = self.currentId()
        if cid == self._page_ids.get("welcome"):
            return bool(self.appimage_path)
        if cid == self._page_ids.get("installtype"):
            return True
        if cid == self._page_ids.get("destination"):
            dest = self._destination_page.get_destination(get_settings()["install_dir"])
            if not dest:
                play_sound("warning")
                QMessageBox.warning(self, "Error", "Please select a destination folder.")
                return False
            self.dest_dir = dest
            if os.path.exists(self.dest_dir):
                play_sound("warning")
                reply = QMessageBox.question(
                    self,
                    "Overwrite?",
                    f"Folder '{self.dest_dir}' already exists.\nExisting files will be backed up.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return False
                self._create_backup(self.dest_dir)
            return True
        if cid == self._page_ids.get("components"):
            if not self._components_page.validate():
                return False
            dest = self.dest_dir or os.path.join(get_settings()["install_dir"], self.app_name or "app")
            if os.path.exists(dest):
                play_sound("warning")
                reply = QMessageBox.question(
                    self,
                    "Overwrite?",
                    f"Folder '{dest}' already exists.\nExisting files will be backed up.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return False
                self._create_backup(dest)
            return True
        return True

    def _create_backup(self, directory: str):
        if not os.path.isdir(directory):
            return
        import uuid

        safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", self.app_name or "unknown")[:64]
        backup_dir = os.path.join(tempfile.gettempdir(), f"aim-backup-{safe_name}-{uuid.uuid4().hex[:8]}")
        try:
            shutil.copytree(directory, backup_dir)
            self._backup_dir = backup_dir
            self._log(f"Backup created: {backup_dir}")
        except OSError as e:
            self._log(f"Warning: backup failed ({e})")

    def _restore_backup(self):
        if not self._backup_dir or not os.path.isdir(self._backup_dir) or not self.dest_dir:
            return
        try:
            if os.path.exists(self.dest_dir):
                shutil.rmtree(self.dest_dir)
            shutil.copytree(self._backup_dir, self.dest_dir, dirs_exist_ok=True)
            self._log("Previous installation restored from backup.")
        except OSError as e:
            self._log(f"Error restoring backup: {e}")

    def _cleanup_backup(self):
        if self._backup_dir and os.path.isdir(self._backup_dir):
            try:
                shutil.rmtree(self._backup_dir)
            except OSError:
                pass
        self._backup_dir = None

    def _do_install(self):
        if self._extraction_started:
            return
        if not self.appimage_path:
            play_sound("error")
            QMessageBox.critical(self, "Error", "No AppImage file selected.")
            self.reject()
            return
        if not self.dest_dir:
            play_sound("error")
            QMessageBox.critical(self, "Error", "Destination folder not set.")
            self.reject()
            return
        if not os.path.isfile(self.appimage_path):
            play_sound("error")
            QMessageBox.critical(self, "Error", f"AppImage file not found:\n{self.appimage_path}")
            self.reject()
            return
        ok, msg = self.validate_install_path(self.dest_dir)
        if not ok:
            play_sound("warning")
            QMessageBox.warning(self, "Invalid Install Path", msg)
            self.reject()
            return
        self._extraction_started = True

        self._wbutton(QWizard.WizardButton.BackButton).setEnabled(False)
        self._wbutton(QWizard.WizardButton.BackButton).hide()
        self._wbutton(QWizard.WizardButton.NextButton).setEnabled(False)

        self._progress_page.set_task("Preparing...", "Extracting AppImage contents")
        self._progress_page.append_log(f"Installing: {self.app_name}")
        self._progress_page.append_log(f"Source: {self.appimage_path}")
        self._progress_page.append_log(f"Destination: {self.dest_dir}")
        self._start_progress_animation()

        want_vt = self._components_page.cb_vt_scan is not None and self._components_page.cb_vt_scan.isChecked()
        if want_vt:
            from niruvi.app.virustotal import get_api_key, scan_file

            api_key = get_api_key()
            if api_key:
                self._progress_page.set_task("Scanning with VirusTotal...")
                self._progress_page.append_log("Uploading to VirusTotal for analysis (may take a minute)...")

                appimage_path = self.appimage_path

                class _VTScanWorker(QThread):
                    finished_scan = pyqtSignal(object)

                    def run(self):
                        self.finished_scan.emit(scan_file(appimage_path, api_key))

                self._vt_worker = _VTScanWorker(self)
                self._vt_worker.finished_scan.connect(self._on_vt_scan_done)
                self._vt_worker.finished.connect(self._vt_worker.deleteLater)
                self._vt_worker.start()
                return

        self._start_extraction()

    def _start_extraction(self):
        if not self.appimage_path or not self.dest_dir or not self.app_name:
            self._log("Missing install path information.")
            return
        self.worker = ExtractionWorker(self.appimage_path, self.dest_dir, self.app_name)
        self.worker.extraction_finished.connect(self._on_extraction_finished)
        self.worker.extraction_error.connect(self._on_extraction_error)
        self.worker.progress_updated.connect(self._on_worker_progress)
        self.worker.log_message.connect(self._on_worker_log)
        start_worker(self.worker)

    def _on_vt_scan_done(self, result: dict):
        if getattr(self, "_vt_scan_aborted", False):
            return
        self._scan_result = result
        status = result.get("status", "skipped")
        malicious = result.get("malicious", 0)
        if status == "flagged":
            play_sound("warning")
            self._progress_page.append_log(
                f"VirusTotal: {malicious} engine(s) flagged this file — {result.get('permalink', '')}"
            )
            reply = QMessageBox.warning(
                self,
                "VirusTotal Flagged",
                f"<b>{malicious} antivirus engine(s)</b> flagged this AppImage as malicious.<br><br>"
                f"SHA-256: <code>{result.get('sha256', '')[:16]}…</code><br>"
                f"Report: <a href='{result.get('permalink', '')}'>{result.get('permalink', '')}</a><br><br>"
                "Installing a file flagged by multiple engines is risky.<br><br>"
                "Continue installing anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                self._vt_scan_aborted = True
                self._progress_page.append_log("Installation aborted after VirusTotal flag.")
                self._restore_backup()
                self._cleanup_backup()
                self.reject()
                return
        elif status == "clean":
            self._progress_page.append_log(
                f"VirusTotal: clean ({result.get('harmless', 0)} harmless, {result.get('undetected', 0)} undetected)."
            )
        else:
            self._progress_page.append_log(
                f"VirusTotal scan skipped: {result.get('error', 'unknown reason')}. Continuing without cloud scan."
            )
        self._start_extraction()

    def _on_worker_progress(self, value: int):
        self._set_real_progress(value)

    def _on_worker_log(self, msg: str):
        self._progress_page.append_log(msg)

    def _on_extraction_finished(self, dest_dir: str, app_name: str):
        self._stop_progress_animation()
        self._set_real_progress(100)
        play_sound("progress")
        self._progress_page.set_task("Verifying...")

        valid, diag_warnings = self._validate_installation(dest_dir)
        if not valid:
            self._progress_page.append_log("Warning: extracted files may be incomplete.")
            msg = "The installation may be incomplete (AppRun missing or not executable)."
            if diag_warnings:
                msg += "\n\n" + "\n".join(diag_warnings[:5])
            play_sound("warning")
            reply = QMessageBox.warning(
                self,
                "Validation Warning",
                msg + "\n\nDo you want to continue anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                self._restore_backup()
                self._cleanup_backup()
                self.reject()
                return
        elif diag_warnings:
            self._progress_page.append_log("Installation completed with warnings:")
            for w in diag_warnings:
                self._progress_page.append_log(f"  • {w}")

        self._progress_page.set_task("Configuring desktop integration...")
        self._progress_page.append_log("Installation complete!")
        play_sound("success")

        from niruvi.config import get_settings
        from niruvi.core.plugins import emit

        try:
            emit(
                "app_installed",
                app_name=app_name,
                app_dir=dest_dir,
                version=get_version(dest_dir),
            )
        except Exception:
            pass

        if get_settings().get("delta_updates", True) and self.appimage_path and os.path.isfile(self.appimage_path):
            try:
                seed_target = os.path.join(dest_dir, f"{app_name}.AppImage")
                shutil.copy2(self.appimage_path, seed_target)
                os.chmod(seed_target, 0o755)
            except OSError as e:
                self._progress_page.append_log(f"Note: could not keep delta seed: {e}")
        self._wbutton(QWizard.WizardButton.NextButton).setEnabled(True)
        self._wbutton(QWizard.WizardButton.FinishButton).setEnabled(True)
        self._wbutton(QWizard.WizardButton.FinishButton).show()
        self._wbutton(QWizard.WizardButton.CancelButton).setEnabled(False)

        try:
            version = get_version(dest_dir)
            metadata = {
                "version": version,
                "install_date": str(Path(dest_dir).stat().st_ctime),
            }
            if self._scan_result:
                metadata["scan_risk"] = self._scan_result.get("risk_level", "")
                metadata["scan_warnings"] = self._scan_result.get("warnings", [])
                if self._scan_result.get("status"):
                    metadata["vt_status"] = self._scan_result.get("status")
                    metadata["vt_malicious"] = self._scan_result.get("malicious", 0)
                    metadata["vt_permalink"] = self._scan_result.get("permalink", "")
            meta_path = os.path.join(dest_dir, ".appimage-manager.json")
            with tempfile.NamedTemporaryFile(
                mode="w", dir=os.path.dirname(meta_path), delete=False, suffix=".tmp"
            ) as tf:
                json.dump(metadata, tf)
                tmp_path = tf.name
            os.replace(tmp_path, meta_path)
        except OSError as e:
            self._progress_page.append_log(f"Warning: could not write metadata ({e})")

        desktop_file_path = None
        shortcut_path = None

        if self._components_page.cb_desktop_file.isChecked():
            try:
                desktop_file_path = create_desktop_entry(dest_dir, app_name, self)
                self._progress_page.append_log(f"Desktop entry: {desktop_file_path or 'failed'}")
            except Exception as e:
                self._progress_page.append_log(f"Desktop entry failed: {e}")

        if self._components_page.cb_desktop_shortcut.isChecked():
            icon_path = None
            if self._desktop_info:
                icon_name = self._desktop_info.get("Icon", "")
                if icon_name:
                    icon_path = find_icon_in_appdir(dest_dir, icon_name)
            if not icon_path:
                for ext in (".png", ".svg", ".xpm"):
                    for root, _, files in os.walk(dest_dir):
                        for f in files:
                            if f.endswith(ext):
                                icon_path = os.path.join(root, f)
                                break
                        if icon_path:
                            break
            try:
                shortcut_path = create_desktop_shortcut(app_name, os.path.join(dest_dir, "AppRun"), icon_path)
                self._progress_page.append_log(f"Desktop shortcut: {shortcut_path or 'failed'}")
            except Exception as e:
                self._progress_page.append_log(f"Desktop shortcut failed: {e}")

        if self._components_page.cb_portable_home.isChecked():
            try:
                home_dir = dest_dir + ".home"
                Path(home_dir).mkdir(exist_ok=True)
                self._progress_page.append_log(f"Portable home: {home_dir}")
            except OSError as e:
                self._progress_page.append_log(f"Portable home failed: {e}")

        if self._components_page.cb_portable_config.isChecked():
            try:
                config_dir = dest_dir + ".config"
                Path(config_dir).mkdir(exist_ok=True)
                self._progress_page.append_log(f"Portable config: {config_dir}")
            except OSError as e:
                self._progress_page.append_log(f"Portable config failed: {e}")

        try:
            refresh_desktop_database()
        except Exception as e:
            self._progress_page.append_log(f"Warning: desktop database refresh failed ({e})")

        try:
            source_sha256 = ""
            if self.appimage_path and os.path.isfile(self.appimage_path):
                from niruvi.core.verification import sha256_file

                source_sha256 = sha256_file(self.appimage_path)
            sandbox_config = {
                "enabled": self._components_page.cb_hardening.isChecked(),
                "hardening": self._components_page.cb_hardening.isChecked(),
                "portable_home": self._components_page.cb_portable_home.isChecked(),
                "portable_config": self._components_page.cb_portable_config.isChecked(),
                "backend": get_settings().get("sandbox_default_backend", "shield"),
            }
            registry = InstallationRegistry()
            record = InstallationRecord(
                name=app_name,
                path=dest_dir,
                version=version,
                desktop_file=desktop_file_path or "",
                desktop_shortcut=shortcut_path or "",
                source_sha256=source_sha256,
                architecture=getattr(self, "_architecture", ""),
                sandbox_config=sandbox_config,
            )
            registry.add(record)
            self._progress_page.append_log("Registered in installation database.")
        except Exception as e:
            self._progress_page.append_log(f"Warning: registry update failed ({e})")

        self._cleanup_backup()

        self._progress_page.set_task("Installation complete!")
        self._progress_page.set_progress(100)

        detail = (
            f"Installed to: {dest_dir}\n"
            f"Desktop integration: {'✓' if desktop_file_path else '—'}\n"
            f"Shortcut: {'✓' if shortcut_path else '—'}"
        )
        self._finish_page.set_completed(app_name, self._icon_pixmap, detail)
        self._progress_page.append_log("Installation complete!")

        self._finish_page.launch_check.setChecked(True)
        self.next()

    def _on_extraction_error(self, error_msg: str):
        logger.error("Extraction failed: %s", error_msg)
        # Ensure we don't touch a deleted worker
        if self._worker_alive():
            self.worker = None
        if error_msg.startswith("JUNEST_PATH:"):
            error_msg = error_msg[len("JUNEST_PATH:") :]
            self._progress_page.set_task("Installation failed", error_msg)
            try:
                from niruvi.installer.junest import suggest_space_free_path

                suggested = suggest_space_free_path(self.dest_dir) if self.dest_dir else ""
            except Exception:
                suggested = ""
            self._restore_backup()
            self._cleanup_backup()
            if suggested and suggested != self.dest_dir:
                reply = QMessageBox.question(
                    self,
                    "JuNest Container Path",
                    f"{error_msg}\n\nInstall to '{suggested}' instead?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self.dest_dir = suggested
                    self._extraction_started = False
                    self._do_install()
                    return
            self.reject()
            return
        self._progress_page.set_task("Installation failed", error_msg)
        if self.dest_dir and os.path.isdir(self.dest_dir):
            try:
                shutil.rmtree(self.dest_dir)
            except OSError:
                pass
        self._restore_backup()
        self._cleanup_backup()
        QMessageBox.critical(
            self, "Installation Error", f"Failed to install: {error_msg}\n\nThe previous version has been restored."
        )
        self.reject()

    def _validate_installation(self, dest_dir: str) -> tuple[bool, list[str]]:
        warnings: list[str] = []
        apprun = os.path.join(dest_dir, "AppRun")
        if not os.path.isfile(apprun):
            warnings.append("AppRun not found after extraction")
            return False, warnings
        if not os.access(apprun, os.X_OK):
            self._progress_page.append_log("Setting AppRun executable...")
            try:
                os.chmod(apprun, 0o755)
            except OSError as e:
                warnings.append(f"Could not set executable: {e}")
                return True, warnings

        from niruvi.app.health_check import check_app_runnable, check_fuse_available

        diag = check_app_runnable(os.path.basename(dest_dir), dest_dir)
        if not diag["healthy"]:
            for issue in diag["issues"]:
                warnings.append(f"Diagnostic: {issue}")
        else:
            self._progress_page.append_log("Pre-flight diagnostics passed.")

        if not check_fuse_available():
            warnings.append("FUSE is not available — AppImage may require extraction to run")
            self._progress_page.append_log("Warning: FUSE not available.")

        if diag.get("warnings"):
            for w in diag["warnings"][:3]:
                self._progress_page.append_log(f"Note: {w}")

        return True, warnings

    def _start_progress_animation(self):
        self._progress_tick = 0
        self._progress_real = -1
        self._progress_timer.start()

    def _stop_progress_animation(self):
        self._progress_timer.stop()

    def _on_progress_tick(self):
        self._progress_tick += 1
        display = max(self._simulated_value(), self._progress_real)
        pct = min(display, 100)
        self._progress_page.set_progress(pct)
        if pct < 20:
            self._progress_page.set_task("Preparing...")
        elif pct < 50:
            self._progress_page.set_task("Extracting AppImage...")
        elif pct < 75:
            self._progress_page.set_task("Configuring...")
        elif pct < 95:
            self._progress_page.set_task("Integrating...")

    def _simulated_value(self):
        t = self._progress_tick
        if t < 10:
            return 5 + t * 2
        elif t < 40:
            return 25 + int((t - 10) * 0.8)
        elif t < 100:
            return 49 + int((t - 40) * 0.35)
        elif t < 200:
            return 70 + int((t - 100) * 0.15)
        else:
            return min(90 + int((t - 200) * 0.03), 99)

    def _set_real_progress(self, value):
        if value > self._progress_real:
            self._progress_real = value
        display = min(max(value, self._simulated_value()), 100)
        self._progress_page.set_progress(display)

    def done(self, result):
        self._stop_progress_animation()
        try:
            if self.worker and self.worker.isRunning():
                self.worker.stop()
                self.worker.wait()
        except RuntimeError:
            pass  # Worker was already deleted
        super().done(result)

    def accept(self):
        p = self.parent()
        if p is not None:
            scan = getattr(p, "scan_installed", None)
            if callable(scan):
                scan()
        should_launch = self._finish_page.should_launch()
        super().accept()
        if should_launch and self.dest_dir:
            apprun = os.path.join(self.dest_dir, "AppRun")
            if os.path.isfile(apprun) and os.access(apprun, os.X_OK):
                try:
                    import subprocess

                    subprocess.Popen([apprun], start_new_session=True)
                except Exception as e:
                    logger.debug("Failed to launch app after install: %s", e, exc_info=True)

    def _worker_alive(self) -> bool:
        """Return True only if the worker object still exists in C++."""
        worker = getattr(self, "worker", None)
        if worker is None:
            return False
        try:
            return bool(worker.isRunning())  # or any harmless method
        except RuntimeError:
            # C++ object deleted; clean up the Python wrapper
            self.worker = None
            return False

    def validate_install_path(self, path: str) -> tuple[bool, str]:
        """Return (ok, message). Blocks install if path is invalid.

        Currently checks for spaces in the path (JuNest/bwrap incompatible)
        and verifies the parent directory exists.
        """
        if " " in path:
            safe = sanitize_install_name(os.path.basename(path))
            suggested = os.path.join(os.path.dirname(path), safe)
            return False, (
                f"This app uses a JuNest container which cannot handle spaces in paths.\nSuggested path: {suggested}"
            )
        if not os.path.exists(os.path.dirname(path)):
            return False, f"Parent directory does not exist: {os.path.dirname(path)}"
        return True, ""

    def reject(self):
        if getattr(self, "_rejecting", False):
            return
        self._rejecting = True
        try:
            play_sound("navigation")
            if self._worker_alive() and self.worker and self.worker.isRunning():
                play_sound("warning")
                reply = QMessageBox.question(
                    self,
                    "Cancel Installation?",
                    "Installation is in progress. Cancel anyway?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                self.worker.stop()
                if not self.worker.wait(3000):
                    logger.warning("ExtractionWorker did not stop gracefully")
            self._restore_backup()
            self._cleanup_backup()
        finally:
            self._rejecting = False
