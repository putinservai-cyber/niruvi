"""Build Wizard — 4-page Windows-style AppImage builder.

Pages: ProjectSetup → Dependencies → BuildConfig → BuildProgress
Supports project file save/load, template gallery, DwarFS toggle.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import (
    QWizard, QWizardPage, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QFileDialog,
    QProgressBar, QTextEdit, QCheckBox, QComboBox,
    QRadioButton, QGroupBox, QFormLayout,
    QMessageBox, QWidget,
)

from niruvi.build.page import BuildWorker, _flatten_appdir
from niruvi.ui.settings import get_settings
from niruvi.ui.report_dialog import ErrorReportDialog, BuildSummaryDialog
from niruvi.utils import get_icon


PROJECT_FILE_FILTER = "Niruvi Project (*.niruviproject);;JSON (*.json)"


TEMPLATES = {
    "python": {
        "name": "Python Application",
        "description": "Python script with optional virtual env",
        "executable": "main.py",
        "version": "1.0.0",
        "icon_hint": "",
    },
    "qt": {
        "name": "Qt Application",
        "description": "PyQt/PySide application with Qt libraries",
        "executable": "app.py",
        "version": "1.0.0",
        "icon_hint": "",
    },
    "cli": {
        "name": "CLI Tool",
        "description": "Command-line binary tool",
        "executable": "app",
        "version": "1.0.0",
        "icon_hint": "",
    },
    "blank": {
        "name": "Blank Project",
        "description": "Start from scratch",
        "executable": "",
        "version": "1.0.0",
        "icon_hint": "",
    },
}


class ProjectSetupPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Project Setup")
        self.setSubTitle("Select the source and configure basic application info.")
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Template gallery
        template_group = QGroupBox("Quick Start Template")
        tform = QFormLayout(template_group)
        self.template_combo = QComboBox()
        for tid, tpl in TEMPLATES.items():
            self.template_combo.addItem(f"{tpl['name']} — {tpl['description']}", tid)
        self.template_combo.currentIndexChanged.connect(self._apply_template)
        tform.addRow("Template:", self.template_combo)
        layout.addWidget(template_group)

        # Source type
        src_group = QGroupBox("Source")
        src_form = QFormLayout(src_group)

        type_row = QHBoxLayout()
        self.pkg_radio = QRadioButton("Package (DEB/RPM/tar)")
        self.pkg_radio.setChecked(True)
        self.pkg_radio.toggled.connect(self._on_source_type_changed)
        self.folder_radio = QRadioButton("Project folder")
        self.folder_radio.toggled.connect(self._on_source_type_changed)
        type_row.addWidget(self.pkg_radio)
        type_row.addWidget(self.folder_radio)
        type_row.addStretch()
        src_form.addRow("Type:", type_row)

        self._pkg_widget = QWidget()
        pkg_row = QHBoxLayout(self._pkg_widget)
        pkg_row.setContentsMargins(0, 0, 0, 0)
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("Drop a file here or click Browse...")
        self.source_edit.setReadOnly(True)
        pkg_row.addWidget(self.source_edit)
        browse_pkg_btn = QPushButton(get_icon("document-open"), "Browse...")
        browse_pkg_btn.clicked.connect(self._browse_source)
        pkg_row.addWidget(browse_pkg_btn)
        src_form.addRow("File:", self._pkg_widget)

        self._folder_widget = QWidget()
        self._folder_widget.setVisible(False)
        folder_row = QHBoxLayout(self._folder_widget)
        folder_row.setContentsMargins(0, 0, 0, 0)
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("Drop a folder here or click Browse...")
        self.folder_edit.setReadOnly(True)
        folder_row.addWidget(self.folder_edit)
        browse_folder_btn = QPushButton(get_icon("folder-open"), "Browse...")
        browse_folder_btn.clicked.connect(self._browse_folder)
        folder_row.addWidget(browse_folder_btn)
        src_form.addRow("Folder:", self._folder_widget)

        self.folder_info_label = QLabel()
        self.folder_info_label.setWordWrap(True)
        self.folder_info_label.setStyleSheet("color: palette(disabled-text); font-size: 9pt;")
        self.folder_info_label.setVisible(False)
        src_form.addRow(self.folder_info_label)

        layout.addWidget(src_group)

        # App info
        info_group = QGroupBox("Application Info")
        info_form = QFormLayout(info_group)

        name_row = QHBoxLayout()
        self.app_name_edit = QLineEdit()
        self.app_name_edit.setPlaceholderText("MyApp")
        name_row.addWidget(self.app_name_edit, 2)
        self.app_version_edit = QLineEdit()
        self.app_version_edit.setPlaceholderText("1.0.0")
        name_row.addWidget(self.app_version_edit, 1)
        info_form.addRow("Name / Version:", name_row)

        self.exec_edit = QLineEdit()
        self.exec_edit.setPlaceholderText("main.py, app, or path relative to AppDir")
        info_form.addRow("Executable:", self.exec_edit)

        icon_row = QHBoxLayout()
        self.icon_edit = QLineEdit()
        self.icon_edit.setPlaceholderText("Optional icon file (.png, .svg)")
        self.icon_edit.setReadOnly(True)
        icon_row.addWidget(self.icon_edit)
        browse_icon_btn = QPushButton(get_icon("document-open"), "Browse...")
        browse_icon_btn.clicked.connect(self._browse_icon)
        icon_row.addWidget(browse_icon_btn)
        info_form.addRow("Icon:", icon_row)

        layout.addWidget(info_group)

        # Drag and drop
        self.setAcceptDrops(True)

    def _apply_template(self, idx: int):
        tid = self.template_combo.itemData(idx)
        tpl = TEMPLATES.get(tid)
        if not tpl:
            return
        self.app_name_edit.setText(tpl["name"].replace(" Application", "").replace(" Tool", ""))
        self.app_version_edit.setText(tpl["version"])
        self.exec_edit.setText(tpl["executable"])
        self.icon_edit.clear()

    def _on_source_type_changed(self):
        is_pkg = self.pkg_radio.isChecked()
        self._pkg_widget.setVisible(is_pkg)
        self._folder_widget.setVisible(not is_pkg)
        self.folder_info_label.setVisible(not is_pkg)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if not urls:
            return
        path = urls[0].toLocalFile()
        if os.path.isdir(path):
            self.folder_radio.setChecked(True)
            self.folder_edit.setText(path)
            folder_name = os.path.basename(path)
            if not self.app_name_edit.text():
                self.app_name_edit.setText(folder_name)
            self._update_folder_info(path)
        elif os.path.isfile(path):
            self.pkg_radio.setChecked(True)
            self.source_edit.setText(path)
            if not self.app_name_edit.text():
                stem = Path(path).stem
                name = stem.split('-')[0] if '-' in stem else stem
                self.app_name_edit.setText(name)

    def _browse_source(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select source package",
            os.path.expanduser("~"),
            "Package files (*.deb *.rpm *.tar.gz *.tar.xz *.tar.bz2 *.tgz *.txz *.tbz2 *.tar);;All files (*)",
        )
        if path:
            self.source_edit.setText(path)
            if not self.app_name_edit.text():
                stem = Path(path).stem
                name = stem.split('-')[0] if '-' in stem else stem
                self.app_name_edit.setText(name)

    def _browse_folder(self):
        dir_path = QFileDialog.getExistingDirectory(
            self, "Select project folder", os.path.expanduser("~"),
        )
        if dir_path:
            self.folder_edit.setText(dir_path)
            folder_name = os.path.basename(dir_path)
            if not self.app_name_edit.text():
                self.app_name_edit.setText(folder_name)
            self._update_folder_info(dir_path)

    def _update_folder_info(self, folder_path: str):
        try:
            total = 0
            file_count = 0
            for root, _dirs, files in os.walk(folder_path):
                for f in files:
                    fp = os.path.join(root, f)
                    total += os.path.getsize(fp)
                    file_count += 1
            info = f"{file_count} files, {self._format_size(total)}"
            self.folder_info_label.setText(info)
        except Exception:
            self.folder_info_label.setText("")

    def _browse_icon(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select icon", os.path.expanduser("~"),
            "Images (*.png *.svg *.xpm);;All files (*)",
        )
        if path:
            self.icon_edit.setText(path)

    def _format_size(self, bytes_val: int) -> str:
        if bytes_val < 1024:
            return f"{bytes_val} B"
        if bytes_val < 1024 * 1024:
            return f"{bytes_val / 1024:.1f} KB"
        if bytes_val < 1024 * 1024 * 1024:
            return f"{bytes_val / (1024 * 1024):.1f} MB"
        return f"{bytes_val / (1024 * 1024 * 1024):.2f} GB"

    def get_source_path(self) -> str:
        return self.source_edit.text() if self.pkg_radio.isChecked() else self.folder_edit.text()

    def is_folder_source(self) -> bool:
        return self.folder_radio.isChecked()


class DependenciesPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Dependencies")
        self.setSubTitle("Review shared library dependencies.")
        layout = QVBoxLayout(self)

        self.scan_btn = QPushButton(get_icon("emblem-system"), "Scan Dependencies")
        self.scan_btn.clicked.connect(self._scan)
        layout.addWidget(self.scan_btn)

        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setStyleSheet(
            "font-family: monospace; font-size: 10pt;"
        )
        layout.addWidget(self.result_text, 1)

        self.status_label = QLabel("Click 'Scan Dependencies' to analyze the AppDir.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

    def _scan(self):
        wizard: "BuildWizard" = self.wizard()
        src = wizard.page(0).get_source_path()
        if not src or not os.path.exists(src):
            self.result_text.setPlainText("No valid source selected yet.")
            return
        self.scan_btn.setEnabled(False)
        self.status_label.setText("Scanning...")
        self.result_text.clear()
        try:
            if wizard.page(0).is_folder_source():
                appdir = src
            else:
                import tempfile
                tmpdir = tempfile.mkdtemp(prefix='niruvi-depscan-')
                from niruvi.build.page import extract_package, detect_package_type
                pkg_type = detect_package_type(src)
                if pkg_type == 'unknown':
                    self.result_text.setPlainText(f"Unsupported package type: {Path(src).suffix}")
                    return
                ok, err = extract_package(src, tmpdir)
                if not ok:
                    self.result_text.setPlainText(f"Extraction failed: {err}")
                    return
                _flatten_appdir(tmpdir)
                appdir = tmpdir

            # Find binaries
            binaries = set()
            for root, _dirs, files in os.walk(appdir):
                for f in files:
                    fp = os.path.join(root, f)
                    if os.path.isfile(fp) and os.access(fp, os.X_OK):
                        try:
                            with open(fp, 'rb') as fh:
                                header = fh.read(4)
                            if header == b'\x7fELF':
                                binaries.add(fp)
                        except Exception:
                            pass

            if not binaries:
                self.result_text.setPlainText("No ELF binaries found in source.")
                self.status_label.setText("No binaries to scan.")
                return

            results = []
            missing_total = set()
            for bp in sorted(binaries):
                try:
                    proc = shutil.which('ldd')
                    if not proc:
                        self.result_text.setPlainText("'ldd' not found on this system.")
                        return
                    result = subprocess.run(
                        ['ldd', bp], capture_output=True, text=True, timeout=30
                    )
                    rel = os.path.relpath(bp, appdir)
                    results.append(f"\n--- {rel} ---")
                    not_found = 0
                    for line in result.stdout.splitlines():
                        if 'not found' in line:
                            parts = line.strip().split()
                            if parts:
                                missing_total.add(parts[0])
                            not_found += 1
                            results.append(f"  MISSING: {line.strip()}")
                        else:
                            results.append(f"  {line.strip()}")
                    if not_found == 0:
                        results.append("  (all libraries resolved)")
                except Exception as e:
                    rel = os.path.relpath(bp, appdir)
                    results.append(f"\n--- {rel} ---")
                    results.append(f"  Error: {e}")

            self.result_text.setPlainText('\n'.join(results))
            total_binaries = len(binaries)
            total_missing = len(missing_total)
            if total_missing:
                self.status_label.setText(
                    f"Scanned {total_binaries} binaries — {total_missing} missing libraries detected."
                )
            else:
                self.status_label.setText(
                    f"Scanned {total_binaries} binaries — all libraries resolved."
                )
        except Exception as e:
            self.result_text.setPlainText(f"Scan failed: {e}")
            self.status_label.setText("Dependency scan encountered an error.")
        finally:
            self.scan_btn.setEnabled(True)


class BuildConfigPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Build Configuration")
        self.setSubTitle("Configure output, signing, and advanced options.")
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Output destination
        dest_group = QGroupBox("Destination")
        dest_form = QFormLayout(dest_group)
        out_row = QHBoxLayout()
        default_out = get_settings().get("build_output_dir", os.path.expanduser("~/Applications"))
        self.output_edit = QLineEdit(default_out)
        self.output_edit.setReadOnly(True)
        out_row.addWidget(self.output_edit)
        out_browse = QPushButton(get_icon("folder-open"), "Browse...")
        out_browse.clicked.connect(self._browse_output)
        out_row.addWidget(out_browse)
        dest_form.addRow("Output folder:", out_row)

        self.copy_to_managed_check = QCheckBox("Copy built AppImage to managed install directory")
        self.copy_to_managed_check.setChecked(True)
        dest_form.addRow(self.copy_to_managed_check)
        layout.addWidget(dest_group)

        # Self-installer
        si_group = QGroupBox("Self-Installing AppImage")
        si_layout = QVBoxLayout(si_group)
        self.self_install_check = QCheckBox("Create self-installing AppImage (not standard portable format)")
        self.self_install_check.toggled.connect(self._on_self_install_toggled)
        si_layout.addWidget(self.self_install_check)

        self._adv_widget = QWidget()
        adv_inner = QVBoxLayout(self._adv_widget)
        adv_inner.setContentsMargins(0, 0, 0, 0)
        adv_group = QGroupBox("Installer Options")
        adv_form = QFormLayout(adv_group)

        self.brand_name_edit = QLineEdit()
        self.brand_name_edit.setPlaceholderText("Same as app name if left empty")
        adv_form.addRow("Brand name:", self.brand_name_edit)

        lic_row = QHBoxLayout()
        self.license_edit = QLineEdit()
        self.license_edit.setPlaceholderText("Optional EULA file...")
        self.license_edit.setReadOnly(True)
        lic_row.addWidget(self.license_edit)
        lic_browse = QPushButton(get_icon("document-open"), "Browse...")
        lic_browse.clicked.connect(lambda: self._browse_file(self.license_edit, "License (*.txt *.md *.rtf)"))
        lic_row.addWidget(lic_browse)
        adv_form.addRow("License:", lic_row)

        pre_row = QHBoxLayout()
        self.pre_script_edit = QLineEdit()
        self.pre_script_edit.setPlaceholderText("Optional pre-install script...")
        self.pre_script_edit.setReadOnly(True)
        pre_row.addWidget(self.pre_script_edit)
        pre_browse = QPushButton(get_icon("document-open"), "Browse...")
        pre_browse.clicked.connect(lambda: self._browse_file(self.pre_script_edit, "Shell (*.sh)"))
        pre_row.addWidget(pre_browse)
        adv_form.addRow("Pre-install:", pre_row)

        post_row = QHBoxLayout()
        self.post_script_edit = QLineEdit()
        self.post_script_edit.setPlaceholderText("Optional post-install script...")
        self.post_script_edit.setReadOnly(True)
        post_row.addWidget(self.post_script_edit)
        post_browse = QPushButton(get_icon("document-open"), "Browse...")
        post_browse.clicked.connect(lambda: self._browse_file(self.post_script_edit, "Shell (*.sh)"))
        post_row.addWidget(post_browse)
        adv_form.addRow("Post-install:", post_row)

        self.updater_url_edit = QLineEdit()
        self.updater_url_edit.setPlaceholderText("https://example.com/updates/update.json")
        adv_form.addRow("Update URL:", self.updater_url_edit)

        flags_row = QHBoxLayout()
        self.rollback_check = QCheckBox("Rollback")
        self.rollback_check.setChecked(True)
        flags_row.addWidget(self.rollback_check)
        self.silent_check = QCheckBox("Silent mode")
        self.silent_check.setChecked(True)
        flags_row.addWidget(self.silent_check)
        self.launch_check = QCheckBox("Launch prompt")
        self.launch_check.setChecked(True)
        flags_row.addWidget(self.launch_check)
        flags_row.addStretch()
        adv_form.addRow("Options:", flags_row)

        adv_inner.addWidget(adv_group)
        self._adv_widget.setVisible(False)
        si_layout.addWidget(self._adv_widget)
        layout.addWidget(si_group)

        # Signing
        sign_group = QGroupBox("Code Signing")
        sign_form = QFormLayout(sign_group)
        sign_row = QHBoxLayout()
        self.sign_check = QCheckBox("GPG-sign the AppImage")
        self.sign_check.toggled.connect(self._on_sign_toggled)
        sign_row.addWidget(self.sign_check)
        self.sign_key_combo = QComboBox()
        self.sign_key_combo.setMinimumWidth(300)
        self.sign_key_combo.setEnabled(False)
        sign_row.addWidget(self.sign_key_combo)
        refresh_btn = QPushButton(get_icon("view-refresh"), "")
        refresh_btn.setToolTip("Refresh available GPG keys")
        refresh_btn.clicked.connect(self._refresh_signing_keys)
        sign_row.addWidget(refresh_btn)
        sign_row.addStretch()
        sign_form.addRow("GPG Key:", sign_row)
        self._refresh_signing_keys()
        layout.addWidget(sign_group)

        # DwarFS
        dwarfs_group = QGroupBox("Compression")
        dwarfs_layout = QHBoxLayout(dwarfs_group)
        self.dwarfs_check = QCheckBox("Use DwarFS compression (smaller images, requires FUSE)")
        self.dwarfs_check.setToolTip("DwarFS provides better compression ratios than gzip/zstd")
        dwarfs_layout.addWidget(self.dwarfs_check)
        dwarfs_layout.addStretch()
        layout.addWidget(dwarfs_group)

        layout.addStretch()

    def _on_sign_toggled(self, checked: bool):
        self.sign_key_combo.setEnabled(checked)

    def _refresh_signing_keys(self):
        from niruvi.core.signing import list_secret_keys
        self.sign_key_combo.clear()
        self.sign_key_combo.addItem("(default key)", "")
        for key in list_secret_keys():
            label = f"{key.name} <{key.email}>  [{key.fingerprint[:16]}...]"
            self.sign_key_combo.addItem(label, key.fingerprint)

    def _on_self_install_toggled(self, checked: bool):
        self._adv_widget.setVisible(checked)

    def _browse_output(self):
        dir_path = QFileDialog.getExistingDirectory(
            self, "Select output directory", self.output_edit.text()
        )
        if dir_path:
            self.output_edit.setText(dir_path)

    def _browse_file(self, edit_widget: QLineEdit, filter_str: str):
        path, _ = QFileDialog.getOpenFileName(self, "Select file", os.path.expanduser("~"), filter_str)
        if path:
            edit_widget.setText(path)


class BuildProgressPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Building AppImage")
        self.setSubTitle("Building...")
        layout = QVBoxLayout(self)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet("font-family: monospace; font-size: 10pt;")
        layout.addWidget(self.log_text, 1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        btn_row = QHBoxLayout()
        self.cancel_btn = QPushButton(get_icon("process-stop"), "Cancel")
        self.cancel_btn.clicked.connect(self._cancel_build)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addStretch()
        self.launch_btn = QPushButton(get_icon("media-playback-start"), "Launch")
        self.launch_btn.clicked.connect(self._launch)
        self.launch_btn.setVisible(False)
        btn_row.addWidget(self.launch_btn)
        layout.addLayout(btn_row)

        self._worker = None
        self._out_path = None

    def _cancel_build(self):
        if self._worker:
            self._worker.stop()
            self.log_text.append("\nBuild cancelled.")
            self.cancel_btn.setEnabled(False)

    def _launch(self):
        if self._out_path and os.path.isfile(self._out_path):
            import subprocess
            try:
                subprocess.Popen([self._out_path], start_new_session=True)
            except Exception as e:
                QMessageBox.warning(self, "Launch Failed", str(e))

    def start_build(self, wizard: "BuildWizard"):
        self.log_text.clear()
        self.progress_bar.setValue(0)
        self.cancel_btn.setEnabled(True)
        self.launch_btn.setVisible(False)
        self._out_path = None
        self.setSubTitle("Building...")

        setup = wizard.page(0)
        config = wizard.page(2)

        src = setup.get_source_path()
        is_folder = setup.is_folder_source()
        output_dir = config.output_edit.text()
        app_name = setup.app_name_edit.text() or None
        app_version = setup.app_version_edit.text() or None
        exec_path = setup.exec_edit.text() or None
        self_installing = config.self_install_check.isChecked()

        self._sign_key = (
            config.sign_key_combo.currentData()
            if config.sign_check.isChecked() and config.sign_key_combo.currentData()
            else None
        )
        self._do_sign = config.sign_check.isChecked()
        self._copy_to_managed = config.copy_to_managed_check.isChecked()

        self._worker = BuildWorker(
            src, output_dir,
            app_name=app_name,
            app_version=app_version,
            self_installing=self_installing,
            installer_style="qt6",
            brand_name=config.brand_name_edit.text() if self_installing else "",
            license_file=config.license_edit.text() if self_installing else "",
            pre_install_script=config.pre_script_edit.text() if self_installing else "",
            post_install_script=config.post_script_edit.text() if self_installing else "",
            enable_rollback=config.rollback_check.isChecked() if self_installing else True,
            enable_silent=config.silent_check.isChecked() if self_installing else True,
            updater_url=config.updater_url_edit.text().strip() if self_installing else "",
            enable_launch_at_finish=config.launch_check.isChecked() if self_installing else True,
            is_folder_source=is_folder,
        )
        self._worker.log.connect(self._on_log)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_log(self, msg: str):
        self.log_text.append(msg)
        sb = self.log_text.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())

    def _on_progress(self, value: int):
        self.progress_bar.setValue(value)

    def _on_finished(self, out_path: str):
        self._out_path = out_path
        self.progress_bar.setValue(100)
        self.cancel_btn.setEnabled(False)
        self.launch_btn.setVisible(True)
        self.setSubTitle("Build complete!")
        self.log_text.append(f"\nBuild successful: {out_path}")

        if self._copy_to_managed:
            self._copy_to_managed_dir(out_path)

        if self._do_sign:
            self.log_text.append("\nSigning AppImage...")
            try:
                from niruvi.core.signing import sign_appimage
                sig_path = sign_appimage(out_path, self._sign_key)
                self.log_text.append(f"Signed: {sig_path}")
            except Exception as e:
                self.log_text.append(f"Signing failed: {e}")

        wizard: "BuildWizard" = self.wizard()
        wizard._build_complete(out_path)

    def _on_error(self, msg: str):
        self.log_text.append(f"\nERROR: {msg}")
        self.progress_bar.setValue(0)
        self.cancel_btn.setEnabled(False)
        self.setSubTitle("Build failed")

        import traceback
        from niruvi.utils.sound_manager import play as play_sound
        play_sound("error")
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

    def _copy_to_managed_dir(self, appimage_path: str):
        install_dir = get_settings().get("install_dir", os.path.expanduser("~/Applications"))
        dest = os.path.join(install_dir, os.path.basename(appimage_path))
        try:
            shutil.copy2(appimage_path, dest)
            os.chmod(dest, 0o755)
            self.log_text.append(f"Copied to managed directory: {dest}")
        except OSError as e:
            self.log_text.append(f"Warning: could not copy to managed directory: {e}")


class BuildWizard(QWizard):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Build AppImage")
        self.setMinimumSize(720, 580)
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)

        self.addPage(ProjectSetupPage())
        self.addPage(DependenciesPage())
        self.addPage(BuildConfigPage())
        self.addPage(BuildProgressPage())

        self._current_project_path = None

        # Toolbar-like buttons for project save/load
        self.setOption(QWizard.WizardOption.HaveCustomButton1, True)
        self.setButtonText(QWizard.WizardButton.CustomButton1, "Load Project")
        self.setOption(QWizard.WizardOption.HaveCustomButton2, True)
        self.setButtonText(QWizard.WizardButton.CustomButton2, "Save Project")

        self.customButtonClicked.connect(self._on_custom_button)

        self.currentIdChanged.connect(self._on_page_changed)

    def _on_custom_button(self, which: int):
        if which == QWizard.WizardButton.CustomButton1:
            self._load_project()
        elif which == QWizard.WizardButton.CustomButton2:
            self._save_project()

    def _on_page_changed(self, page_id: int):
        if page_id == 3:
            self._start_build()

    def _start_build(self):
        progress_page: BuildProgressPage = self.page(3)
        progress_page.start_build(self)

    def _build_complete(self, out_path: str):
        import hashlib
        import subprocess

        is_valid, warnings = self._verify_appimage(out_path)
        file_size = os.path.getsize(out_path) if os.path.isfile(out_path) else 0
        is_elf = False
        is_exec = os.access(out_path, os.X_OK)
        architecture = ""
        sha256 = ""
        app_type = ""
        try:
            with open(out_path, 'rb') as f:
                header = f.read(20)
                is_elf = header[:4] == b'\x7fELF'
                if is_elf and len(header) >= 20:
                    ei_class = header[4]
                    ei_data = header[5]
                    e_machine_bytes = header[18:20]
                    arch_map = {0: "None", 3: "i386", 62: "x86_64",
                                40: "ARM", 183: "AArch64", 20: "PowerPC",
                                21: "PowerPC64", 43: "SPARC"}
                    arch = "32-bit " if ei_class == 1 else "64-bit " if ei_class == 2 else ""
                    arch += "LE" if ei_data == 1 else "BE" if ei_data == 2 else ""
                    import struct
                    e_machine = struct.unpack('<H' if ei_data == 1 else '>H', e_machine_bytes)[0]
                    arch_name = arch_map.get(e_machine, f"machine={e_machine}")
                    architecture = f"{arch} {arch_name}"
                    if len(header) >= 12:
                        f.seek(8)
                        type_check = f.read(4)
                        app_type = "Type 2" if type_check[:2] == b'AI' else "Type 1"
                f.seek(0)
                sha256 = hashlib.sha256(f.read()).hexdigest()
        except Exception:
            pass

        summary = BuildSummaryDialog(
            self,
            appimage_path=out_path,
            file_size=file_size,
            is_elf=is_elf,
            is_executable=is_exec,
            validation_warnings=warnings,
            architecture=architecture,
            sha256=sha256,
            app_type=app_type,
        )
        summary.exec()

    def _verify_appimage(self, path: str):
        warnings = []
        if not os.path.isfile(path):
            return False, ["File not found after build."]
        size = os.path.getsize(path)
        if size < 1024:
            warnings.append(f"AppImage is very small. It may not be valid.")
        if size > 4 * 1024 * 1024 * 1024:
            warnings.append(f"AppImage is very large. Some systems may not run it.")
        is_exec = os.access(path, os.X_OK)
        if not is_exec:
            warnings.append("AppImage is not executable. Users will need: chmod +x")
        is_elf = False
        try:
            with open(path, 'rb') as f:
                is_elf = f.read(4) == b'\x7fELF'
            if not is_elf:
                warnings.append("File does not have a valid ELF header.")
        except Exception:
            warnings.append("Could not read file header for validation.")
        try:
            result = subprocess.run(
                [path, "--appimage-help"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                warnings.append(f"AppImage runtime check failed (exit {result.returncode}).")
        except (subprocess.TimeoutExpired, OSError) as e:
            warnings.append(f"Could not verify AppImage: {e}")
        return is_elf and is_exec, warnings

    def _save_project(self):
        setup = self.page(0)
        config = self.page(2)
        data = {
            "version": 1,
            "project": {
                "source_type": "folder" if setup.is_folder_source() else "package",
                "source_path": setup.get_source_path(),
                "app_name": setup.app_name_edit.text(),
                "app_version": setup.app_version_edit.text(),
                "executable": setup.exec_edit.text(),
                "icon": setup.icon_edit.text(),
                "template": setup.template_combo.currentData(),
            },
            "build": {
                "output_dir": config.output_edit.text(),
                "copy_to_managed": config.copy_to_managed_check.isChecked(),
                "self_installing": config.self_install_check.isChecked(),
                "brand_name": config.brand_name_edit.text(),
                "license_file": config.license_edit.text(),
                "pre_install_script": config.pre_script_edit.text(),
                "post_install_script": config.post_script_edit.text(),
                "updater_url": config.updater_url_edit.text(),
                "rollback": config.rollback_check.isChecked(),
                "silent": config.silent_check.isChecked(),
                "launch_prompt": config.launch_check.isChecked(),
                "sign": config.sign_check.isChecked(),
                "sign_key": config.sign_key_combo.currentData(),
                "dwarfs": config.dwarfs_check.isChecked(),
            },
        }
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Project", os.path.expanduser("~"),
            PROJECT_FILE_FILTER,
        )
        if path:
            if not path.endswith('.niruviproject') and not path.endswith('.json'):
                path += '.niruviproject'
            try:
                with open(path, 'w') as f:
                    json.dump(data, f, indent=2)
                self._current_project_path = path
                self.setWindowTitle(f"Build AppImage — {Path(path).name}")
            except Exception as e:
                QMessageBox.warning(self, "Save Failed", str(e))

    def _load_project(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Project", os.path.expanduser("~"),
            PROJECT_FILE_FILTER,
        )
        if not path:
            return
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.warning(self, "Load Failed", f"Could not read project file:\n{e}")
            return

        proj = data.get("project", {})
        build = data.get("build", {})

        setup = self.page(0)
        config = self.page(2)

        # Project tab
        source_type = proj.get("source_type", "package")
        if source_type == "folder":
            setup.folder_radio.setChecked(True)
            setup.folder_edit.setText(proj.get("source_path", ""))
        else:
            setup.pkg_radio.setChecked(True)
            setup.source_edit.setText(proj.get("source_path", ""))
        setup.app_name_edit.setText(proj.get("app_name", ""))
        setup.app_version_edit.setText(proj.get("app_version", ""))
        setup.exec_edit.setText(proj.get("executable", ""))
        setup.icon_edit.setText(proj.get("icon", ""))
        tpl = proj.get("template", "blank")
        idx = setup.template_combo.findData(tpl)
        if idx >= 0:
            setup.template_combo.setCurrentIndex(idx)

        # Build config tab
        config.output_edit.setText(build.get("output_dir", os.path.expanduser("~/Applications")))
        config.copy_to_managed_check.setChecked(build.get("copy_to_managed", True))
        config.self_install_check.setChecked(build.get("self_installing", False))
        config.brand_name_edit.setText(build.get("brand_name", ""))
        config.license_edit.setText(build.get("license_file", ""))
        config.pre_script_edit.setText(build.get("pre_install_script", ""))
        config.post_script_edit.setText(build.get("post_install_script", ""))
        config.updater_url_edit.setText(build.get("updater_url", ""))
        config.rollback_check.setChecked(build.get("rollback", True))
        config.silent_check.setChecked(build.get("silent", True))
        config.launch_check.setChecked(build.get("launch_prompt", True))
        config.sign_check.setChecked(build.get("sign", False))
        config.dwarfs_check.setChecked(build.get("dwarfs", False))
        sign_key = build.get("sign_key", "")
        if sign_key:
            idx = config.sign_key_combo.findData(sign_key)
            if idx >= 0:
                config.sign_key_combo.setCurrentIndex(idx)

        self._current_project_path = path
        self.setWindowTitle(f"Build AppImage — {Path(path).name}")
