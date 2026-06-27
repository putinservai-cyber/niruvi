import os
import subprocess
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QFileDialog,
    QProgressBar, QTextEdit, QDialogButtonBox,
    QMessageBox, QGroupBox, QCheckBox, QRadioButton,
    QWidget, QScrollArea, QFrame,
)

from niruvi.build_page import BuildWorker
from niruvi.settings import get_settings
from niruvi.report_dialog import ErrorReportDialog, BuildSummaryDialog
from niruvi.utils import get_icon


_SECTION_STYLE = """
QGroupBox {{
    font-weight: bold;
    border: 1px solid palette(mid);
    border-radius: 6px;
    margin-top: 8px;
    padding-top: 14px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 2px 8px;
    background: palette(window);
}}
"""


class BuildDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Build AppImage")
        self.setMinimumSize(640, 620)
        self._worker = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        scroll.setWidget(content)
        inner = QVBoxLayout(content)
        inner.setSpacing(8)

        # ── Source Section ──
        src_group = QGroupBox("Source")
        src_group.setStyleSheet(_SECTION_STYLE)
        src_form = QFormLayout(src_group)
        src_form.setSpacing(6)

        src_type_row = QHBoxLayout()
        self.pkg_radio = QRadioButton("Package (DEB/RPM/tar)")
        self.pkg_radio.setChecked(True)
        self.pkg_radio.toggled.connect(self._on_source_type_changed)
        self.folder_radio = QRadioButton("Project folder")
        self.folder_radio.toggled.connect(self._on_source_type_changed)
        src_type_row.addWidget(self.pkg_radio)
        src_type_row.addWidget(self.folder_radio)
        src_type_row.addStretch()
        src_form.addRow("Type:", src_type_row)

        self._pkg_widget = QWidget()
        pkg_row = QHBoxLayout(self._pkg_widget)
        pkg_row.setContentsMargins(0, 0, 0, 0)
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("Select a DEB, RPM, or tar archive...")
        self.source_edit.setReadOnly(True)
        pkg_row.addWidget(self.source_edit)
        self.browse_pkg_btn = QPushButton(get_icon("document-open"), "Browse...")
        self.browse_pkg_btn.clicked.connect(self._browse_source)
        pkg_row.addWidget(self.browse_pkg_btn)
        src_form.addRow("File:", self._pkg_widget)

        self._folder_widget = QWidget()
        self._folder_widget.setVisible(False)
        folder_row = QHBoxLayout(self._folder_widget)
        folder_row.setContentsMargins(0, 0, 0, 0)
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("Select a local project folder...")
        self.folder_edit.setReadOnly(True)
        folder_row.addWidget(self.folder_edit)
        self.browse_folder_btn = QPushButton(get_icon("folder-open"), "Browse...")
        self.browse_folder_btn.clicked.connect(self._browse_folder)
        folder_row.addWidget(self.browse_folder_btn)
        src_form.addRow("Folder:", self._folder_widget)

        self.folder_info_label = QLabel()
        self.folder_info_label.setWordWrap(True)
        self.folder_info_label.setStyleSheet("color: palette(disabled-text); font-size: 9pt; padding-left: 4px;")
        self.folder_info_label.setVisible(False)
        src_form.addRow(self.folder_info_label)

        self.folder_hint = QLabel(
            "The folder contents are copied directly into the AppDir. "
            "Make sure your project has an executable entry point "
            "(main.py, app.py, or a compiled binary)."
        )
        self.folder_hint.setWordWrap(True)
        self.folder_hint.setStyleSheet("color: palette(text); font-size: 9pt; padding: 6px 8px; background: palette(window); border: 1px solid palette(mid); border-radius: 4px;")
        self.folder_hint.setVisible(False)
        src_form.addRow(self.folder_hint)

        name_version_row = QHBoxLayout()
        self.app_name_edit = QLineEdit()
        self.app_name_edit.setPlaceholderText("Auto-detected")
        name_version_row.addWidget(self.app_name_edit, 2)
        self.app_version_edit = QLineEdit()
        self.app_version_edit.setPlaceholderText("Auto-detected")
        name_version_row.addWidget(self.app_version_edit, 1)
        src_form.addRow("Name / Version:", name_version_row)

        inner.addWidget(src_group)

        # ── Destination Section ──
        dest_group = QGroupBox("Destination")
        dest_group.setStyleSheet(_SECTION_STYLE)
        dest_layout = QVBoxLayout(dest_group)
        dest_layout.setSpacing(6)

        out_row = QHBoxLayout()
        default_out = get_settings().get("build_output_dir", os.path.expanduser("~/Applications"))
        self.output_edit = QLineEdit(default_out)
        self.output_edit.setReadOnly(True)
        out_row.addWidget(self.output_edit)
        out_browse = QPushButton(get_icon("folder-open"), "Browse...")
        out_browse.clicked.connect(self._browse_output)
        out_row.addWidget(out_browse)
        dest_layout.addLayout(out_row)

        self.copy_to_managed_check = QCheckBox("Copy built AppImage to managed install directory")
        self.copy_to_managed_check.setChecked(True)
        dest_layout.addWidget(self.copy_to_managed_check)

        inner.addWidget(dest_group)

        # ── Self-Installer Section ──
        self.self_install_group = QGroupBox("Self-Installing AppImage")
        self.self_install_group.setStyleSheet(_SECTION_STYLE)
        si_layout = QVBoxLayout(self.self_install_group)
        si_layout.setSpacing(4)

        self.self_install_check = QCheckBox("Create self-installing AppImage (not standard portable format)")
        self.self_install_check.toggled.connect(self._on_self_install_toggled)
        si_layout.addWidget(self.self_install_check)

        self._advanced_widget = QWidget()
        adv_inner = QVBoxLayout(self._advanced_widget)
        adv_inner.setContentsMargins(0, 0, 0, 0)
        adv_inner.setSpacing(6)

        adv_group = QGroupBox("Installer Configuration")
        adv_group.setStyleSheet(_SECTION_STYLE)
        adv_form = QFormLayout(adv_group)
        adv_form.setSpacing(4)

        self.brand_name_edit = QLineEdit()
        self.brand_name_edit.setPlaceholderText("Same as app name if left empty")
        adv_form.addRow("Brand name:", self.brand_name_edit)

        lic_row = QHBoxLayout()
        self.license_edit = QLineEdit()
        self.license_edit.setPlaceholderText("Optional EULA / license agreement...")
        self.license_edit.setReadOnly(True)
        lic_row.addWidget(self.license_edit)
        lic_browse = QPushButton(get_icon("document-open"), "Browse...")
        lic_browse.clicked.connect(lambda: self._browse_file(self.license_edit, "License file (*.txt *.md *.rtf)"))
        lic_row.addWidget(lic_browse)
        adv_form.addRow("License:", lic_row)

        pre_row = QHBoxLayout()
        self.pre_script_edit = QLineEdit()
        self.pre_script_edit.setPlaceholderText("Optional script before installation...")
        self.pre_script_edit.setReadOnly(True)
        pre_row.addWidget(self.pre_script_edit)
        pre_browse = QPushButton(get_icon("document-open"), "Browse...")
        pre_browse.clicked.connect(lambda: self._browse_file(self.pre_script_edit, "Shell script (*.sh)"))
        pre_row.addWidget(pre_browse)
        adv_form.addRow("Pre-install:", pre_row)

        post_row = QHBoxLayout()
        self.post_script_edit = QLineEdit()
        self.post_script_edit.setPlaceholderText("Optional script after installation...")
        self.post_script_edit.setReadOnly(True)
        post_row.addWidget(self.post_script_edit)
        post_browse = QPushButton(get_icon("document-open"), "Browse...")
        post_browse.clicked.connect(lambda: self._browse_file(self.post_script_edit, "Shell script (*.sh)"))
        post_row.addWidget(post_browse)
        adv_form.addRow("Post-install:", post_row)

        comp_label = QLabel(
            "Optional components users can choose during install.\n"
            "Format: <code>id:Display Name:enabled</code>  e.g. <code>docs:Documentation:true</code>"
        )
        comp_label.setWordWrap(True)
        self.components_edit = QTextEdit()
        self.components_edit.setPlaceholderText("core:Core Files:true\nplugins:Plugins:false")
        self.components_edit.setMaximumHeight(80)
        adv_form.addRow("Components:", comp_label)
        adv_form.addRow(self.components_edit)

        self.updater_url_edit = QLineEdit()
        self.updater_url_edit.setPlaceholderText("https://example.com/updates/update.json")
        adv_form.addRow("Update URL:", self.updater_url_edit)

        msg_row = QHBoxLayout()
        self.welcome_msg_edit = QTextEdit()
        self.welcome_msg_edit.setPlaceholderText("Custom welcome message...")
        self.welcome_msg_edit.setMaximumHeight(60)
        msg_row.addWidget(self.welcome_msg_edit)
        self.finish_msg_edit = QTextEdit()
        self.finish_msg_edit.setPlaceholderText("Custom finish message...")
        self.finish_msg_edit.setMaximumHeight(60)
        msg_row.addWidget(self.finish_msg_edit)
        adv_form.addRow("Welcome / Finish:", msg_row)

        flags_row = QHBoxLayout()
        self.rollback_check = QCheckBox("Rollback")
        self.rollback_check.setChecked(True)
        self.rollback_check.setToolTip("Backup and restore on failure")
        flags_row.addWidget(self.rollback_check)
        self.silent_check = QCheckBox("Silent mode")
        self.silent_check.setChecked(True)
        self.silent_check.setToolTip("Support --unattended flag")
        flags_row.addWidget(self.silent_check)
        self.launch_check = QCheckBox("Launch prompt")
        self.launch_check.setChecked(True)
        self.launch_check.setToolTip("Show 'Launch now?' after install")
        flags_row.addWidget(self.launch_check)
        flags_row.addStretch()
        adv_form.addRow("Options:", flags_row)

        adv_inner.addWidget(adv_group)
        self._advanced_widget.setVisible(False)
        si_layout.addWidget(self._advanced_widget)

        inner.addWidget(self.self_install_group)

        inner.addStretch()
        layout.addWidget(scroll, 1)

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
        self.build_btn = QPushButton(get_icon("emblem-system"), "Build AppImage")
        self.build_btn.clicked.connect(self._start_build)
        self.build_btn.setStyleSheet("QPushButton { padding: 8px 24px; font-weight: bold; }")
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
        self._advanced_widget.setVisible(checked)
        if checked and not getattr(self, '_self_install_warned', False):
            self._self_install_warned = True
            QMessageBox.information(
                self, "Self-Installing AppImage",
                "Standard AppImages run anywhere without installation.\n\n"
                "A self-installing AppImage must be installed before first use\n"
                "and creates files outside the AppImage (desktop entries, app data,\n"
                "uninstaller).\n\n"
                "This is the same method Niruvi itself uses, and is suitable for\n"
                "applications that need desktop integration or a managed install lifecycle."
            )

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
                return "Unsupported package format. Please select a DEB, RPM, or tar archive."
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

        # Try --appimage-help (handled by runtime, won't launch the app)
        try:
            result = subprocess.run(
                [path, "--appimage-help"],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode != 0:
                warnings.append(f"AppImage runtime check failed (exit {result.returncode}).")
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
            installer_style="qt6",
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
