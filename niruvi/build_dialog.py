import os
import subprocess
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon, QFont
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QFileDialog,
    QProgressBar, QTextEdit, QDialogButtonBox,
    QMessageBox, QGroupBox, QCheckBox, QRadioButton,
    QComboBox, QWidget, QScrollArea, QTabWidget,
    QFrame,
)

from niruvi.build_page import BuildWorker
from niruvi.settings import get_settings
from niruvi.report_dialog import ErrorReportDialog, BuildSummaryDialog
from niruvi.utils import get_icon


class InstallerBuilderConfig(QDialog):
    """Configuration dialog for GUI installer options — branding, messages, updates, license."""

    def __init__(self, parent=None,
                 updater_url="", welcome_message="",
                 finish_message="", enable_launch=True,
                 brand_name="", license_file=""):
        super().__init__(parent)
        self.setWindowTitle("Installer Settings")
        self.setMinimumSize(560, 520)
        self.updater_url = updater_url
        self.welcome_message = welcome_message
        self.finish_message = finish_message
        self.enable_launch = enable_launch
        self.brand_name = brand_name
        self.license_file = license_file
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        tabs = QTabWidget()

        # ── Branding Tab ──
        branding_tab = QWidget()
        branding_form = QFormLayout(branding_tab)
        branding_form.setSpacing(8)

        help_label = QLabel("Customize the look and feel of the installer wizard.")
        help_label.setWordWrap(True)
        branding_form.addRow(help_label)

        self.brand_edit = QLineEdit(self.brand_name)
        self.brand_edit.setPlaceholderText("Same as app name if left empty")
        branding_form.addRow("Brand name:", self.brand_edit)

        # License file picker
        lic_row = QHBoxLayout()
        self.lic_edit = QLineEdit(self.license_file)
        self.lic_edit.setReadOnly(True)
        self.lic_edit.setPlaceholderText("Optional EULA / license agreement file...")
        lic_row.addWidget(self.lic_edit)
        lic_browse = QPushButton(QIcon.fromTheme("document-open"), "Browse...")
        lic_browse.clicked.connect(self._browse_license)
        lic_row.addWidget(lic_browse)
        lic_clear = QPushButton(QIcon.fromTheme("edit-clear"), "Clear")
        lic_clear.clicked.connect(lambda: self.lic_edit.clear())
        lic_row.addWidget(lic_clear)
        branding_form.addRow("License file:", lic_row)

        self.welcome_edit = QTextEdit()
        self.welcome_edit.setPlainText(self.welcome_message)
        self.welcome_edit.setPlaceholderText(
            "Welcome message shown on the first page.\n"
            "Leave empty for the default welcome text."
        )
        self.welcome_edit.setMaximumHeight(100)
        branding_form.addRow("Welcome text:", self.welcome_edit)

        self.finish_edit = QTextEdit()
        self.finish_edit.setPlainText(self.finish_message)
        self.finish_edit.setPlaceholderText(
            "Message shown after installation completes.\n"
            "Leave empty for the default finish text."
        )
        self.finish_edit.setMaximumHeight(100)
        branding_form.addRow("Finish text:", self.finish_edit)

        launch_layout = QHBoxLayout()
        self.launch_cb = QCheckBox("Show 'Launch now?' prompt after installation")
        self.launch_cb.setChecked(self.enable_launch)
        launch_layout.addWidget(self.launch_cb)
        launch_layout.addStretch()
        branding_form.addRow(launch_layout)

        tabs.addTab(branding_tab, "Branding & Messages")

        # ── Updates Tab ──
        updates_tab = QWidget()
        updates_form = QFormLayout(updates_tab)
        updates_form.setSpacing(8)

        update_help = QLabel(
            "Configure automatic updates for the installed application.\n\n"
            "The updater checks a JSON manifest hosted on your server\n"
            "for new versions. When an update is found, users are prompted\n"
            "to download and install it."
        )
        update_help.setWordWrap(True)
        updates_form.addRow(update_help)

        self.url_edit = QLineEdit(self.updater_url)
        self.url_edit.setPlaceholderText("https://example.com/updates/update.json")
        self.url_edit.setToolTip("URL to update manifest JSON.\nLeave empty to skip updater generation.")
        updates_form.addRow("Update manifest URL:", self.url_edit)

        url_example = QLabel(
            "<small>Expected JSON format:<br>"
            "<code>{\"version\": \"2.0\", \"download_url\": \"...\", "
            "\"sha256\": \"...\", \"changelog\": \"...\"}</code></small>"
        )
        url_example.setWordWrap(True)
        updates_form.addRow(url_example)

        tabs.addTab(updates_tab, "Updates")

        # ── Preview Tab ──
        preview_tab = QWidget()
        preview_layout = QVBoxLayout(preview_tab)
        preview_layout.setSpacing(8)

        preview_help = QLabel(
            "The InstallBuilder-style wizard walks users through these pages:"
        )
        preview_help.setWordWrap(True)
        preview_layout.addWidget(preview_help)

        pages = [
            ("Welcome", "Branded intro with welcome message"),
            ("License", "EULA display with Accept/Decline"),
            ("Directory", "Choose installation path"),
            ("Components", "Select optional components to install"),
            ("Summary", "Review selections before installing"),
            ("Progress", "Extraction with status updates"),
            ("Finish", "Success message with launch prompt"),
        ]
        for page, desc in pages:
            row = QHBoxLayout()
            dot = QLabel("\u25cf")
            dot.setStyleSheet("color: #4a90d9; font-size: 16px;")
            row.addWidget(dot)
            row.addWidget(QLabel(f"<b>{page}</b>  \u2014  {desc}"))
            row.addStretch()
            preview_layout.addLayout(row)

        preview_layout.addStretch()
        preview_note = QLabel(
            "<small>Supports Back/Next navigation, silent/unattended mode,<br>"
            "and optional auto-updater with SHA256 verification.</small>"
        )
        preview_note.setWordWrap(True)
        preview_layout.addWidget(preview_note)

        tabs.addTab(preview_tab, "Preview")

        layout.addWidget(tabs)

        # ── Buttons ──
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _browse_license(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select license file",
            os.path.expanduser("~"),
            "License files (*.txt *.md *.rtf);;All files (*)",
        )
        if path:
            self.lic_edit.setText(path)

    def _on_accept(self):
        self.brand_name = self.brand_edit.text().strip()
        self.license_file = self.lic_edit.text().strip()
        self.welcome_message = self.welcome_edit.toPlainText()
        self.finish_message = self.finish_edit.toPlainText()
        self.enable_launch = self.launch_cb.isChecked()
        self.updater_url = self.url_edit.text().strip()
        self.accept()


class BuildDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Build AppImage")
        self.setMinimumSize(620, 540)
        self._worker = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(6)

        # Source type: package file or project folder
        source_type_layout = QHBoxLayout()
        self.pkg_radio = QRadioButton("Package file (DEB/RPM/tar)")
        self.pkg_radio.setChecked(True)
        self.pkg_radio.setToolTip("Select a traditional Linux package (DEB, RPM, or tar archive)\nto extract and repackage as an AppImage.")
        self.pkg_radio.toggled.connect(self._on_source_type_changed)
        self.folder_radio = QRadioButton("Project folder")
        self.folder_radio.setToolTip("Select a local project directory.\nThe folder contents are copied directly into the AppImage.\nUse this for packaging your own applications without creating a DEB or RPM first.")
        self.folder_radio.toggled.connect(self._on_source_type_changed)
        source_type_layout.addWidget(self.pkg_radio)
        source_type_layout.addWidget(self.folder_radio)
        source_type_layout.addStretch()
        form.addRow("Source type:", source_type_layout)

        # Package source
        self._pkg_widget = QWidget()
        pkg_layout = QHBoxLayout(self._pkg_widget)
        pkg_layout.setContentsMargins(0, 0, 0, 0)
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("Select a DEB, RPM, or tar archive...")
        self.source_edit.setReadOnly(True)
        pkg_layout.addWidget(self.source_edit)
        self.browse_pkg_btn = QPushButton(QIcon.fromTheme("document-open"), "Browse...")
        self.browse_pkg_btn.clicked.connect(self._browse_source)
        pkg_layout.addWidget(self.browse_pkg_btn)
        form.addRow("Source package:", self._pkg_widget)

        # Folder source
        self._folder_widget = QWidget()
        self._folder_widget.setVisible(False)
        folder_layout = QHBoxLayout(self._folder_widget)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("Select a local project folder to package as AppImage...")
        self.folder_edit.setReadOnly(True)
        folder_layout.addWidget(self.folder_edit)
        self.browse_folder_btn = QPushButton(QIcon.fromTheme("folder-open"), "Browse...")
        self.browse_folder_btn.clicked.connect(self._browse_folder)
        folder_layout.addWidget(self.browse_folder_btn)
        self.folder_info_label = QLabel()
        self.folder_info_label.setWordWrap(True)
        self.folder_info_label.setStyleSheet("color: palette(disabled-text); font-size: 9pt;")
        form.addRow("Project folder:", self._folder_widget)
        form.addRow(self.folder_info_label)
        self.folder_hint = QLabel(
            "💡 The folder's contents will be copied directly into the AppDir. "
            "Make sure your project has an executable entry point "
            "(main.py, app.py, or a compiled binary)."
        )
        self.folder_hint.setWordWrap(True)
        self.folder_hint.setStyleSheet(
            "color: #666; font-size: 9pt; padding: 4px 8px; "
            "background: palette(window); border: 1px solid palette(mid); border-radius: 4px;"
        )
        self.folder_hint.setVisible(False)
        form.addRow(self.folder_hint)

        self.app_name_edit = QLineEdit()
        self.app_name_edit.setPlaceholderText("Auto-detected from filename or folder name if left empty")
        form.addRow("App name:", self.app_name_edit)

        self.app_version_edit = QLineEdit()
        self.app_version_edit.setPlaceholderText("Auto-detected if left empty")
        form.addRow("App version:", self.app_version_edit)

        out_layout = QHBoxLayout()
        default_out = get_settings().get("build_output_dir", os.path.expanduser("~/Applications"))
        self.output_edit = QLineEdit(default_out)
        self.output_edit.setReadOnly(True)
        out_layout.addWidget(self.output_edit)
        out_browse = QPushButton(QIcon.fromTheme("folder-open"), "Browse...")
        out_browse.clicked.connect(self._browse_output)
        out_layout.addWidget(out_browse)
        form.addRow("Output directory:", out_layout)

        layout.addLayout(form)

        # ── Self-installing option ──
        self_install_group = QGroupBox("Self-Installing AppImage")
        self_install_layout = QVBoxLayout(self_install_group)

        self.self_install_check = QCheckBox("Create self-installing AppImage (not standard portable format)")
        self.self_install_check.toggled.connect(self._on_self_install_toggled)
        self_install_layout.addWidget(self.self_install_check)

        # Installer style selector (hidden until self-installing is checked)
        self._style_row_widget = QWidget()
        style_row = QHBoxLayout(self._style_row_widget)
        style_row.setContentsMargins(0, 0, 0, 0)
        style_label = QLabel("Installer UI style:")
        self.style_combo = QComboBox()
        self.style_combo.addItem("Wizard (zenity/kdialog)", "wizard")
        self.style_combo.addItem("macOS Installer style", "macos")
        self.style_combo.addItem("Minimal (terminal only)", "minimal")
        self.style_combo.addItem("InstallBuilder style", "installbuilder")
        self.style_combo.currentIndexChanged.connect(self._on_style_changed)
        style_row.addWidget(style_label)
        style_row.addWidget(self.style_combo, 1)
        self.installbuilder_btn = QPushButton("Installer Settings...")
        self.installbuilder_btn.setVisible(False)
        self.installbuilder_btn.clicked.connect(self._open_installbuilder_config)
        style_row.addWidget(self.installbuilder_btn)
        self._style_row_widget.setVisible(False)
        self_install_layout.addWidget(self._style_row_widget)

        self.style_description = QLabel()
        self.style_description.setWordWrap(True)
        self.style_description.setVisible(False)
        self_install_layout.addWidget(self.style_description)

        # Advanced options (hidden until self-installing is checked)
        self._advanced_widget = QWidget()
        advanced_layout = QVBoxLayout(self._advanced_widget)
        advanced_layout.setContentsMargins(0, 0, 0, 0)

        advanced_group = QGroupBox("Advanced Installer Options")
        adv_form = QFormLayout(advanced_group)

        # Brand name
        self.brand_name_edit = QLineEdit()
        self.brand_name_edit.setPlaceholderText("Same as app name if left empty")
        adv_form.addRow("Brand name:", self.brand_name_edit)

        # License file
        lic_row = QHBoxLayout()
        self.license_edit = QLineEdit()
        self.license_edit.setPlaceholderText("Optional EULA / license agreement text file...")
        self.license_edit.setReadOnly(True)
        lic_row.addWidget(self.license_edit)
        lic_browse = QPushButton(QIcon.fromTheme("document-open"), "Browse...")
        lic_browse.clicked.connect(lambda: self._browse_file(self.license_edit, "License file (*.txt *.md *.rtf)"))
        lic_row.addWidget(lic_browse)
        lic_clear = QPushButton(QIcon.fromTheme("edit-clear"), "Clear")
        lic_clear.clicked.connect(lambda: self.license_edit.clear())
        lic_row.addWidget(lic_clear)
        adv_form.addRow("License:", lic_row)

        # Pre-install script
        pre_row = QHBoxLayout()
        self.pre_script_edit = QLineEdit()
        self.pre_script_edit.setPlaceholderText("Optional script to run before installation...")
        self.pre_script_edit.setReadOnly(True)
        pre_row.addWidget(self.pre_script_edit)
        pre_browse = QPushButton(QIcon.fromTheme("document-open"), "Browse...")
        pre_browse.clicked.connect(lambda: self._browse_file(self.pre_script_edit, "Shell script (*.sh)"))
        pre_row.addWidget(pre_browse)
        pre_clear = QPushButton(QIcon.fromTheme("edit-clear"), "Clear")
        pre_clear.clicked.connect(lambda: self.pre_script_edit.clear())
        pre_row.addWidget(pre_clear)
        adv_form.addRow("Pre-install script:", pre_row)

        # Post-install script
        post_row = QHBoxLayout()
        self.post_script_edit = QLineEdit()
        self.post_script_edit.setPlaceholderText("Optional script to run after installation...")
        self.post_script_edit.setReadOnly(True)
        post_row.addWidget(self.post_script_edit)
        post_browse = QPushButton(QIcon.fromTheme("document-open"), "Browse...")
        post_browse.clicked.connect(lambda: self._browse_file(self.post_script_edit, "Shell script (*.sh)"))
        post_row.addWidget(post_browse)
        post_clear = QPushButton(QIcon.fromTheme("edit-clear"), "Clear")
        post_clear.clicked.connect(lambda: self.post_script_edit.clear())
        post_row.addWidget(post_clear)
        adv_form.addRow("Post-install script:", post_row)

        # Components
        comp_label = QLabel(
            "Optional components users can choose during install.\n"
            "Format: id:Display Name:enabled_by_default\n"
            "Example: docs:Documentation:true"
        )
        comp_label.setWordWrap(True)
        self.components_edit = QTextEdit()
        self.components_edit.setPlaceholderText(
            "core:Core Files:true\nplugins:Plugins:false\ndocs:Documentation:true"
        )
        self.components_edit.setMaximumHeight(100)
        adv_form.addRow(comp_label)
        adv_form.addRow(self.components_edit)

        # Updater URL
        self.updater_url_edit = QLineEdit()
        self.updater_url_edit.setPlaceholderText("https://example.com/updates/update.json")
        self.updater_url_edit.setToolTip("URL to update manifest JSON. Leave empty to skip updater generation.")
        adv_form.addRow("Update URL:", self.updater_url_edit)

        # Welcome message
        self.welcome_msg_edit = QTextEdit()
        self.welcome_msg_edit.setPlaceholderText("Custom welcome message for the installer (optional)")
        self.welcome_msg_edit.setMaximumHeight(60)
        adv_form.addRow("Welcome text:", self.welcome_msg_edit)

        # Finish message
        self.finish_msg_edit = QTextEdit()
        self.finish_msg_edit.setPlaceholderText("Custom finish message shown after installation (optional)")
        self.finish_msg_edit.setMaximumHeight(60)
        adv_form.addRow("Finish text:", self.finish_msg_edit)

        # Feature flags
        self.rollback_check = QCheckBox("Enable rollback (backup and restore on failure)")
        self.rollback_check.setChecked(True)
        adv_form.addRow(self.rollback_check)

        self.silent_check = QCheckBox("Enable silent/unattended mode (--unattended flag)")
        self.silent_check.setChecked(True)
        adv_form.addRow(self.silent_check)

        self.launch_check = QCheckBox("Show 'Launch now?' prompt after installation")
        self.launch_check.setChecked(True)
        adv_form.addRow(self.launch_check)

        advanced_layout.addWidget(advanced_group)
        self._advanced_widget.setVisible(False)
        self_install_layout.addWidget(self._advanced_widget)

        self.copy_to_managed_check = QCheckBox("Copy built AppImage to install directory")
        self.copy_to_managed_check.setChecked(True)
        self.copy_to_managed_check.setToolTip(
            "After building, copy the resulting AppImage into the managed install directory\n"
            "so it appears in the installed apps list."
        )
        self_install_layout.addWidget(self.copy_to_managed_check)

        layout.addWidget(self_install_group)

        # ── Progress and log ──
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setVisible(False)
        layout.addWidget(self.log_text, 1)

        # ── Build button ──
        btn_layout = QHBoxLayout()
        self.build_btn = QPushButton(get_icon("emblem-system", "applications-utilities", "document-export"), "Build AppImage")
        self.build_btn.clicked.connect(self._start_build)
        self.build_btn.setStyleSheet("QPushButton { padding: 8px 20px; font-weight: bold; }")
        btn_layout.addStretch()
        btn_layout.addWidget(self.build_btn)
        layout.addLayout(btn_layout)

        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    def _browse_file(self, edit_widget: QLineEdit, filter_str: str):
        path, _ = QFileDialog.getOpenFileName(self, "Select file", os.path.expanduser("~"), filter_str)
        if path:
            edit_widget.setText(path)

    def _parse_components(self) -> list:
        text = self.components_edit.toPlainText().strip()
        if not text:
            return []
        result = []
        for line in text.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split(':', 3)
            if len(parts) >= 2:
                cid = parts[0].strip()
                label = parts[1].strip()
                default = parts[2].strip().lower() == 'true' if len(parts) > 2 else True
                desc = parts[3].strip() if len(parts) > 3 else ""
                result.append({"id": cid, "label": label, "default": default, "description": desc})
        return result

    def _on_self_install_toggled(self, checked: bool):
        self._style_row_widget.setVisible(checked)
        self.style_description.setVisible(checked)
        self._advanced_widget.setVisible(checked)
        if checked:
            QMessageBox.warning(
                self, "Self-Installing AppImage",
                "Standard AppImages run anywhere without installation.\n\n"
                "A self-installing AppImage must be installed before first use\n"
                "and creates files outside the AppImage (desktop entries, app data,\n"
                "uninstaller).\n\n"
                "This is the same method Niruvi itself uses, and is suitable for\n"
                "applications that need desktop integration or a managed install lifecycle."
            )
            self._update_style_description()

    def _on_style_changed(self):
        self._update_style_description()
        is_gui = self.style_combo.currentData() != "minimal"
        self.installbuilder_btn.setVisible(is_gui)

    def _open_installbuilder_config(self):
        dlg = InstallerBuilderConfig(
            self,
            updater_url=self.updater_url_edit.text().strip(),
            welcome_message=self.welcome_msg_edit.toPlainText(),
            finish_message=self.finish_msg_edit.toPlainText(),
            enable_launch=self.launch_check.isChecked(),
            brand_name=self.brand_name_edit.text(),
            license_file=self.license_edit.text().strip(),
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.updater_url_edit.setText(dlg.updater_url)
            self.welcome_msg_edit.setPlainText(dlg.welcome_message)
            self.finish_msg_edit.setPlainText(dlg.finish_message)
            self.launch_check.setChecked(dlg.enable_launch)
            if dlg.brand_name:
                self.brand_name_edit.setText(dlg.brand_name)
            if dlg.license_file:
                self.license_edit.setText(dlg.license_file)

    def _update_style_description(self):
        style = self.style_combo.currentData()
        descs = {
            "wizard": "Uses zenity/kdialog GUI dialogs with terminal fallback. "
                       "Standard dialog-based flow familiar to most Linux users.",
            "macos": "Step-by-step wizard resembling macOS Installer: Welcome \u2192 "
                     "Destination \u2192 Installation \u2192 Summary. Uses zenity/kdialog "
                     "with progress bar during extraction.",
            "minimal": "Terminal-only output with no GUI dependencies. "
                       "Fast and works in headless/SSH environments with no display server.",
            "installbuilder": "Professional multi-page wizard with Back/Next navigation. "
                              "Pages: Welcome \u2192 License \u2192 Directory \u2192 Components \u2192 "
                              "Summary \u2192 Progress \u2192 Finish. Includes optional updater.",
        }
        self.style_description.setText(descs.get(style, ""))

    def _on_source_type_changed(self):
        is_pkg = self.pkg_radio.isChecked()
        self._pkg_widget.setVisible(is_pkg)
        self._folder_widget.setVisible(not is_pkg)
        self.folder_info_label.setVisible(not is_pkg)
        self.folder_hint.setVisible(not is_pkg)

    def _browse_source(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select source package",
            os.path.expanduser("~"),
            "Package files (*.deb *.rpm *.tar.gz *.tar.xz *.tar.bz2 *.tgz *.txz *.tbz2 *.tar);;All files (*)",
        )
        if path:
            self.source_edit.setText(path)
            stem = Path(path).stem
            if not self.app_name_edit.text():
                name = stem.split('-')[0] if '-' in stem else stem
                self.app_name_edit.setText(name)

    def _browse_folder(self):
        dir_path = QFileDialog.getExistingDirectory(
            self, "Select project folder",
            os.path.expanduser("~"),
        )
        if dir_path:
            self.folder_edit.setText(dir_path)
            folder_name = os.path.basename(dir_path)
            if not self.app_name_edit.text():
                self.app_name_edit.setText(folder_name)
            self._update_folder_info(dir_path)

    def _update_folder_info(self, folder_path: str):
        """Show helpful info about the selected folder: file count, size, detected entry point."""
        try:
            total = 0
            file_count = 0
            max_depth = 0
            has_python = False
            has_executable = False
            has_apprun = False
            for root, dirs, files in os.walk(folder_path):
                depth = root[len(folder_path):].count(os.sep)
                max_depth = max(max_depth, depth)
                for f in files:
                    fp = os.path.join(root, f)
                    total += os.path.getsize(fp)
                    file_count += 1
                    if f == "AppRun":
                        has_apprun = True
                    if f.endswith(".py"):
                        has_python = True
                    if os.access(fp, os.X_OK) and os.path.isfile(fp):
                        has_executable = True
            info = f"{file_count} files, {self._format_size(total)}, {max_depth} directory levels"
            if has_apprun:
                info += " — has AppRun entry point"
            elif has_python:
                info += " — Python project detected"
            elif has_executable:
                info += " — has executable files"
            self.folder_info_label.setText(info)
        except Exception:
            self.folder_info_label.setText("")

    def _browse_output(self):
        dir_path = QFileDialog.getExistingDirectory(
            self, "Select output directory", self.output_edit.text()
        )
        if dir_path:
            self.output_edit.setText(dir_path)

    def _validate_source(self) -> str | None:
        """Validate the source before building. Returns None if OK, or an error message."""
        is_pkg = self.pkg_radio.isChecked()
        if is_pkg:
            src = self.source_edit.text()
            if not src:
                return "Please select a source package file first."
            if not os.path.isfile(src):
                return f"The file doesn't exist:\n{src}"
            size = os.path.getsize(src)
            if size == 0:
                return f"The source file is empty:\n{src}"
            if size > 2 * 1024 * 1024 * 1024:
                return f"The source file is very large ({self._format_size(size)}). AppImage builds may fail with files over 2 GB."
            low = src.lower()
            if not any(low.endswith(e) for e in ('.deb', '.rpm', '.tar.gz', '.tar.xz', '.tar.bz2', '.tgz', '.txz', '.tbz2', '.tar')):
                return f"Unsupported package format. Please select a DEB, RPM, or tar archive."
        else:
            folder = self.folder_edit.text()
            if not folder:
                return "Please select a project folder first."
            if not os.path.isdir(folder):
                return f"The folder doesn't exist:\n{folder}"
            entries = os.listdir(folder)
            cleaned = [e for e in entries if not e.startswith('.') and e != '__pycache__']
            if not cleaned:
                return f"The selected folder appears to be empty:\n{folder}"
        return None

    def _verify_appimage(self, path: str) -> tuple[bool, list[str]]:
        """Verify a built AppImage. Returns (is_valid, warnings)."""
        warnings = []
        if not os.path.isfile(path):
            return False, ["File not found after build."]
        size = os.path.getsize(path)
        if size < 1024:
            warnings.append(f"AppImage is very small ({self._format_size(size)}). It may not be valid.")
        if size > 4 * 1024 * 1024 * 1024:
            warnings.append(f"AppImage is very large ({self._format_size(size)}). Some systems may not run it.")
        is_exec = os.access(path, os.X_OK)
        if not is_exec:
            warnings.append("AppImage is not executable. Users will need to run: chmod +x")
        is_elf = False
        try:
            with open(path, 'rb') as f:
                header = f.read(4)
                is_elf = header == b'\x7fELF'
            if not is_elf:
                warnings.append("File does not have a valid ELF header. It may not run.")
        except Exception:
            warnings.append("Could not read file header for validation.")

        # Quick sanity: try running --version if it's a self-installing AppImage
        try:
            result = subprocess.run(
                [path, "--version"],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                output = result.stdout.strip()[:100]
                self.log_text.append(f"AppImage version check: {output}")
            else:
                warnings.append(f"AppImage returned exit code {result.returncode} on version check.")
        except (subprocess.TimeoutExpired, OSError) as e:
            warnings.append(f"Could not verify AppImage: {e}")

        return is_elf and is_exec, warnings

    def _start_build(self):
        # Validate source
        error = self._validate_source()
        if error:
            dlg = ErrorReportDialog(
                self,
                title="Cannot Start Build",
                summary="There's a problem with the source you selected.",
                details=error,
                suggestions=[
                    "Double-check the file or folder path.",
                    "Make sure the file is not corrupted or empty.",
                    "For packages, use DEB, RPM, or tar.gz/tar.xz format.",
                    "For folders, choose a directory that contains application files.",
                ],
            )
            dlg.exec()
            return

        src = self.source_edit.text() if self.pkg_radio.isChecked() else self.folder_edit.text()
        is_folder = self.folder_radio.isChecked()

        self.log_text.clear()
        self.log_text.setVisible(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.build_btn.setEnabled(False)
        self.button_box.setEnabled(False)

        self.log_text.append("Starting build...")
        self.log_text.append(f"Source: {src}")
        self.log_text.append(f"Output: {self.output_edit.text()}")
        self.log_text.append(f"Mode: {'Project folder' if is_folder else 'Package file'}")
        self.log_text.append("")

        self_installing = self.self_install_check.isChecked()

        common_kw = dict(
            app_name=self.app_name_edit.text() or None,
            app_version=self.app_version_edit.text() or None,
            self_installing=self_installing,
            installer_style=self.style_combo.currentData() if self_installing else "wizard",
            brand_name=self.brand_name_edit.text() if self_installing else "",
            license_file=self.license_edit.text() if self_installing else "",
            components=self._parse_components() if self_installing and self.components_edit.toPlainText().strip() else None,
            pre_install_script=self.pre_script_edit.text() if self_installing else "",
            post_install_script=self.post_script_edit.text() if self_installing else "",
            enable_rollback=self.rollback_check.isChecked() if self_installing else True,
            enable_silent=self.silent_check.isChecked() if self_installing else True,
            updater_url=self.updater_url_edit.text().strip() if self_installing else "",
            welcome_message=self.welcome_msg_edit.toPlainText().strip() if self_installing else "",
            finish_message=self.finish_msg_edit.toPlainText().strip() if self_installing else "",
            enable_launch_at_finish=self.launch_check.isChecked() if self_installing else True,
            is_folder_source=is_folder,
        )

        self._worker = BuildWorker(src, self.output_edit.text(), **common_kw)
        self._worker.log.connect(self._on_log)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_log(self, msg: str):
        self.log_text.append(msg)
        self.log_text.verticalScrollBar().setValue(
            self.log_text.verticalScrollBar().maximum()
        )

    def _on_progress(self, value: int):
        self.progress_bar.setValue(value)

    def _on_finished(self, out_path: str):
        self.log_text.append(f"\n<b>Build successful: {out_path}</b>")
        self.progress_bar.setValue(100)
        self.build_btn.setEnabled(True)
        self.button_box.setEnabled(True)

        if self.copy_to_managed_check.isChecked():
            self._copy_to_managed(out_path)

        # Post-build verification
        is_valid, warnings = self._verify_appimage(out_path)

        # Show summary dialog
        file_size = os.path.getsize(out_path) if os.path.isfile(out_path) else 0
        is_elf = False
        is_exec = os.access(out_path, os.X_OK)
        try:
            with open(out_path, 'rb') as f:
                is_elf = f.read(4) == b'\x7fELF'
        except Exception:
            pass

        summary = BuildSummaryDialog(
            self,
            appimage_path=out_path,
            file_size=file_size,
            is_elf=is_elf,
            is_executable=is_exec,
            validation_warnings=warnings,
        )
        summary.exec()
        self.accept()

    def _on_error(self, msg: str):
        self.log_text.append(f"\n<b style='color: red;'>ERROR: {msg}</b>")
        self.progress_bar.setValue(0)
        self.build_btn.setEnabled(True)
        self.button_box.setEnabled(True)
        import traceback
        suggestions = ErrorReportDialog.suggest_for_build_error(msg)
        dlg = ErrorReportDialog(
            self,
            title="Build Failed",
            summary="The AppImage build did not complete successfully.",
            details=msg,
            suggestions=suggestions,
            technical=traceback.format_exc(),
            log_text=self.log_text.toPlainText(),
        )
        dlg.exec()

    def _copy_to_managed(self, appimage_path: str):
        install_dir = get_settings().get("install_dir", os.path.expanduser("~/Applications"))
        dest = os.path.join(install_dir, os.path.basename(appimage_path))
        try:
            import shutil
            shutil.copy2(appimage_path, dest)
            os.chmod(dest, 0o755)
            self.log_text.append(f"Copied to managed directory: {dest}")
        except OSError as e:
            self.log_text.append(f"Warning: could not copy to managed directory: {e}")

    @staticmethod
    def _format_size(bytes_val: int) -> str:
        if bytes_val < 1024:
            return f"{bytes_val} B"
        if bytes_val < 1024 * 1024:
            return f"{bytes_val / 1024:.1f} KB"
        if bytes_val < 1024 * 1024 * 1024:
            return f"{bytes_val / (1024 * 1024):.1f} MB"
        return f"{bytes_val / (1024 * 1024 * 1024):.2f} GB"
