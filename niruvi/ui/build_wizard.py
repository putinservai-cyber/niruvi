"""Build Wizard — 4-page Windows-style AppImage builder.

Pages: ProjectSetup → Dependencies → BuildConfig → BuildProgress
Supports project file save/load and DwarFS toggle.
"""

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from niruvi.build.page import BuildWorker, _flatten_appdir
from niruvi.core.worker import start_worker
from niruvi.ui.report_dialog import ErrorReportDialog
from niruvi.ui.settings import get_settings
from niruvi.utils import get_icon
from niruvi.utils.sound_manager import play as play_sound
from niruvi.utils.sound_manager import play_and
from niruvi.utils.styles import MONO_FONT_STYLE, format_size
from niruvi.utils.theme_engine import style

logger = logging.getLogger(__name__)

PROJECT_FILE_FILTER = "Niruvi Project (*.niruviproject);;JSON (*.json)"


class ProjectSetupPage(QWizardPage):
    def _wizard(self) -> "BuildWizard":
        wiz = self.wizard()
        assert isinstance(wiz, BuildWizard)
        return wiz

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Project Setup")
        self.setSubTitle("Select the source and configure basic application info.")

        outer_layout = QVBoxLayout(self)
        outer_layout.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer_layout.addWidget(scroll)

        inner = QWidget()
        scroll.setWidget(inner)
        layout = QVBoxLayout(inner)
        layout.setSpacing(10)

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
        browse_pkg_btn.clicked.connect(lambda: play_and("click", self._browse_source))
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
        browse_folder_btn.clicked.connect(lambda: play_and("click", self._browse_folder))
        folder_row.addWidget(browse_folder_btn)
        src_form.addRow("Folder:", self._folder_widget)

        self.folder_info_label = QLabel()
        self.folder_info_label.setWordWrap(True)
        self.folder_info_label.setStyleSheet(style("color: {colors.subtle}; font-size: 9pt;"))
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
        browse_icon_btn.clicked.connect(lambda: play_and("click", self._browse_icon))
        icon_row.addWidget(browse_icon_btn)
        info_form.addRow("Icon:", icon_row)

        layout.addWidget(info_group)

        self.source_edit.textChanged.connect(self.completeChanged.emit)
        self.folder_edit.textChanged.connect(self.completeChanged.emit)

        # Drag and drop
        self.setAcceptDrops(True)

    def initializePage(self):
        wiz = self._wizard()
        b = wiz.button(QWizard.WizardButton.BackButton)
        if b:
            b.setVisible(False)

    def isComplete(self):
        if self.pkg_radio.isChecked():
            return bool(self.source_edit.text())
        return bool(self.folder_edit.text())

    def _on_source_type_changed(self):
        is_pkg = self.pkg_radio.isChecked()
        self._pkg_widget.setVisible(is_pkg)
        self._folder_widget.setVisible(not is_pkg)
        self.folder_info_label.setVisible(not is_pkg)
        self.completeChanged.emit()

    def dragEnterEvent(self, event: QDragEnterEvent | None):
        if event is None:
            return
        mime = event.mimeData()
        if mime is not None and mime.hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent | None):
        if event is None:
            return
        mime = event.mimeData()
        if mime is None:
            return
        urls = mime.urls()
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
                name = stem.split("-")[0] if "-" in stem else stem
                self.app_name_edit.setText(name)

    def _browse_source(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select source package",
            os.path.expanduser("~"),
            "Package files (*.deb *.rpm *.tar.gz *.tar.xz *.tar.bz2 *.tgz *.txz *.tbz2 *.tar);;All files (*)",
        )
        if path:
            self.source_edit.setText(path)
            if not self.app_name_edit.text():
                stem = Path(path).stem
                name = stem.split("-")[0] if "-" in stem else stem
                self.app_name_edit.setText(name)

    def _browse_folder(self):
        dir_path = QFileDialog.getExistingDirectory(
            self,
            "Select project folder",
            os.path.expanduser("~"),
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
            info = f"{file_count} files, {format_size(total)}"
            self.folder_info_label.setText(info)
        except Exception as e:
            logger.debug("Failed to update folder info: %s", e, exc_info=True)
            self.folder_info_label.setText("")

    def _browse_icon(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select icon",
            os.path.expanduser("~"),
            "Images (*.png *.svg *.xpm);;All files (*)",
        )
        if path:
            self.icon_edit.setText(path)

    def get_source_path(self) -> str:
        return self.source_edit.text() if self.pkg_radio.isChecked() else self.folder_edit.text()

    def is_folder_source(self) -> bool:
        return self.folder_radio.isChecked()


class DependenciesPage(QWizardPage):
    def _wizard(self) -> "BuildWizard":
        wiz = self.wizard()
        assert isinstance(wiz, BuildWizard)
        return wiz

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Dependencies")
        self.setSubTitle("Review shared library dependencies.")
        layout = QVBoxLayout(self)

        self.scan_btn = QPushButton(get_icon("emblem-system"), "Scan Dependencies")
        self.scan_btn.clicked.connect(lambda: play_and("click", self._scan))
        layout.addWidget(self.scan_btn)

        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setStyleSheet(MONO_FONT_STYLE)
        layout.addWidget(self.result_text, 1)

        self.status_label = QLabel("Click 'Scan Dependencies' to analyze the AppDir.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

    def _scan(self):
        wizard = self._wizard()
        src = wizard._setup_page().get_source_path()
        if not src or not os.path.exists(src):
            self.result_text.setPlainText("No valid source selected yet.")
            return
        self.scan_btn.setEnabled(False)
        self.status_label.setText("Scanning...")
        self.result_text.clear()
        tmpdir = None
        try:
            if wizard._setup_page().is_folder_source():
                appdir = src
            else:
                import tempfile

                tmpdir = tempfile.mkdtemp(prefix="niruvi-depscan-")
                from niruvi.build.page import detect_package_type, extract_package

                pkg_type = detect_package_type(src)
                if pkg_type == "unknown":
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
                            with open(fp, "rb") as fh:
                                header = fh.read(4)
                            if header == b"\x7fELF":
                                binaries.add(fp)
                        except Exception as e:
                            logger.debug("Failed to read binary header %s: %s", fp, e, exc_info=True)

            if not binaries:
                self.result_text.setPlainText("No ELF binaries found in source.")
                self.status_label.setText("No binaries to scan.")
                return

            results = []
            missing_total = set()
            for bp in sorted(binaries):
                try:
                    proc = shutil.which("ldd")
                    if not proc:
                        self.result_text.setPlainText("'ldd' not found on this system.")
                        return
                    result = subprocess.run(["ldd", bp], capture_output=True, text=True, timeout=30)
                    rel = os.path.relpath(bp, appdir)
                    results.append(f"\n--- {rel} ---")
                    not_found = 0
                    for line in result.stdout.splitlines():
                        if "not found" in line:
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

            self.result_text.setPlainText("\n".join(results))
            total_binaries = len(binaries)
            total_missing = len(missing_total)
            if total_missing:
                self.status_label.setText(
                    f"Scanned {total_binaries} binaries — {total_missing} missing libraries detected."
                )
            else:
                self.status_label.setText(f"Scanned {total_binaries} binaries — all libraries resolved.")
        except Exception as e:
            self.result_text.setPlainText(f"Scan failed: {e}")
            self.status_label.setText("Dependency scan encountered an error.")
        finally:
            self.scan_btn.setEnabled(True)
            if tmpdir:
                shutil.rmtree(tmpdir, ignore_errors=True)


class BuildConfigPage(QWizardPage):
    def _wizard(self) -> "BuildWizard":
        wiz = self.wizard()
        assert isinstance(wiz, BuildWizard)
        return wiz

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Build Configuration")
        self.setSubTitle("Configure output, signing, and compression options.")

        self.setButtonText(QWizard.WizardButton.NextButton, "Build")

        outer_layout = QVBoxLayout(self)
        outer_layout.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer_layout.addWidget(scroll)

        inner = QWidget()
        scroll.setWidget(inner)
        layout = QVBoxLayout(inner)
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
        out_browse.clicked.connect(lambda: play_and("click", self._browse_output))
        out_row.addWidget(out_browse)
        dest_form.addRow("Output folder:", out_row)

        self.copy_to_managed_check = QCheckBox("Copy built AppImage to managed install directory")
        self.copy_to_managed_check.setChecked(True)
        dest_form.addRow(self.copy_to_managed_check)
        layout.addWidget(dest_group)

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
        refresh_btn.clicked.connect(lambda: play_and("click", self._refresh_signing_keys))
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

    def _on_sign_toggled(self, checked: bool):
        self.sign_key_combo.setEnabled(checked)

    def _refresh_signing_keys(self):
        from niruvi.core.signing import list_secret_keys

        self.sign_key_combo.clear()
        self.sign_key_combo.addItem("(default key)", "")
        for key in list_secret_keys():
            label = f"{key.name} <{key.email}>  [{key.fingerprint[:16]}...]"
            self.sign_key_combo.addItem(label, key.fingerprint)

    def _browse_output(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select output directory", self.output_edit.text())
        if dir_path:
            self.output_edit.setText(dir_path)

    def _browse_file(self, edit_widget: QLineEdit, filter_str: str):
        path, _ = QFileDialog.getOpenFileName(self, "Select file", os.path.expanduser("~"), filter_str)
        if path:
            edit_widget.setText(path)


class BuildProgressPage(QWizardPage):
    def _wizard(self) -> "BuildWizard":
        wiz = self.wizard()
        assert isinstance(wiz, BuildWizard)
        return wiz

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Building AppImage")
        self.setSubTitle("Building...")
        layout = QVBoxLayout(self)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet(MONO_FONT_STYLE)
        layout.addWidget(self.log_text, 1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        btn_row = QHBoxLayout()
        self.cancel_btn = QPushButton(get_icon("process-stop"), "Cancel")
        self.cancel_btn.clicked.connect(lambda: play_and("click", self._cancel_build))
        btn_row.addWidget(self.cancel_btn)
        btn_row.addStretch()
        self.close_btn = QPushButton(get_icon("window-close"), "Close")
        self.close_btn.clicked.connect(lambda: play_and("click", self._close))
        self.close_btn.setVisible(False)
        btn_row.addWidget(self.close_btn)
        self.launch_btn = QPushButton(get_icon("media-playback-start"), "Launch")
        self.launch_btn.clicked.connect(lambda: play_and("click", self._launch))
        self.launch_btn.setVisible(False)
        btn_row.addWidget(self.launch_btn)
        layout.addLayout(btn_row)

        self._worker = None
        self._out_path = None

    def isFinalPage(self):
        return True

    def _hide_wizard_buttons(self):
        wiz = self._wizard()
        for btn_id in (
            QWizard.WizardButton.BackButton,
            QWizard.WizardButton.NextButton,
            QWizard.WizardButton.FinishButton,
            QWizard.WizardButton.CancelButton,
        ):
            b = wiz.button(btn_id)
            if b:
                b.setVisible(False)

    def initializePage(self):
        self._hide_wizard_buttons()
        QTimer.singleShot(0, self._hide_wizard_buttons)

    def _close(self):
        wiz = self._wizard()
        wiz.reject()

    def _cancel_build(self):
        if self._worker:
            self._worker.stop()
            self.log_text.append("\nBuild cancelled.")
            self.cancel_btn.setEnabled(False)
            self.cancel_btn.setVisible(False)
            self.close_btn.setVisible(True)

    def _launch(self):
        if self._out_path and os.path.isfile(self._out_path):
            import subprocess

            try:
                subprocess.Popen([self._out_path], start_new_session=True)
            except Exception as e:
                play_sound("warning")
                QMessageBox.warning(self, "Launch Failed", str(e))

    def start_build(self, wizard: "BuildWizard"):
        self.log_text.clear()
        self.progress_bar.setValue(0)
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.setVisible(True)
        self.close_btn.setVisible(False)
        self.launch_btn.setVisible(False)
        self._out_path = None
        self.setSubTitle("Building...")
        self._hide_wizard_buttons()

        setup = wizard._setup_page()
        config = wizard._config_page()

        src = setup.get_source_path()
        is_folder = setup.is_folder_source()
        output_dir = config.output_edit.text()
        app_name = setup.app_name_edit.text() or None
        app_version = setup.app_version_edit.text() or None

        self._sign_key = (
            config.sign_key_combo.currentData()
            if config.sign_check.isChecked() and config.sign_key_combo.currentData()
            else None
        )
        self._do_sign = config.sign_check.isChecked()
        self._copy_to_managed = config.copy_to_managed_check.isChecked()

        self._worker = BuildWorker(
            src,
            output_dir,
            app_name=app_name,
            app_version=app_version,
            self_installing=False,
            is_folder_source=is_folder,
            config=get_settings(),
        )
        self._worker.log.connect(self._on_log)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        start_worker(self._worker)

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
        self.cancel_btn.setVisible(False)
        self.close_btn.setVisible(True)
        self.launch_btn.setVisible(True)
        play_sound("success")
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

        wizard = self._wizard()
        wizard._build_complete(out_path)

    def _on_error(self, msg: str):
        self.log_text.append(f"\nERROR: {msg}")
        self.progress_bar.setValue(0)
        self.cancel_btn.setVisible(False)
        self.close_btn.setVisible(True)
        self.setSubTitle("Build failed")

        import traceback

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

        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowMaximizeButtonHint)
        self.setFixedSize(720, 580)
        self.currentIdChanged.connect(self._on_page_changed)
        self.rejected.connect(lambda: play_sound("navigation"))

    def _setup_page(self) -> ProjectSetupPage:
        page = self.page(0)
        assert isinstance(page, ProjectSetupPage)
        return page

    def _config_page(self) -> BuildConfigPage:
        page = self.page(2)
        assert isinstance(page, BuildConfigPage)
        return page

    def _progress_page(self) -> BuildProgressPage:
        page = self.page(3)
        assert isinstance(page, BuildProgressPage)
        return page

    def _on_custom_button(self, which: int):
        if which == QWizard.WizardButton.CustomButton1:
            self._load_project()
        elif which == QWizard.WizardButton.CustomButton2:
            self._save_project()

    def _on_page_changed(self, page_id: int):
        play_sound("navigation")
        if page_id == 3:
            self._start_build()

    def _start_build(self):
        self._progress_page().start_build(self)

    def _build_complete(self, out_path: str):
        _is_valid, _warnings = self._verify_appimage(out_path)

    def _verify_appimage(self, path: str):
        warnings = []
        if not os.path.isfile(path):
            return False, ["File not found after build."]
        size = os.path.getsize(path)
        if size < 1024:
            warnings.append("AppImage is very small. It may not be valid.")
        if size > 4 * 1024 * 1024 * 1024:
            warnings.append("AppImage is very large. Some systems may not run it.")
        is_exec = os.access(path, os.X_OK)
        if not is_exec:
            warnings.append("AppImage is not executable. Users will need: chmod +x")
        is_elf = False
        try:
            with open(path, "rb") as f:
                is_elf = f.read(4) == b"\x7fELF"
            if not is_elf:
                warnings.append("File does not have a valid ELF header.")
        except Exception as e:
            logger.debug("Failed to read file header for validation: %s", e, exc_info=True)
            warnings.append("Could not read file header for validation.")
        try:
            result = subprocess.run(
                [path, "--appimage-help"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                warnings.append(f"AppImage runtime check failed (exit {result.returncode}).")
        except (subprocess.TimeoutExpired, OSError) as e:
            warnings.append(f"Could not verify AppImage: {e}")
        return is_elf and is_exec, warnings

    def _save_project(self):
        setup = self._setup_page()
        config = self._config_page()
        data = {
            "version": 1,
            "project": {
                "source_type": "folder" if setup.is_folder_source() else "package",
                "source_path": setup.get_source_path(),
                "app_name": setup.app_name_edit.text(),
                "app_version": setup.app_version_edit.text(),
                "executable": setup.exec_edit.text(),
                "icon": setup.icon_edit.text(),
            },
            "build": {
                "output_dir": config.output_edit.text(),
                "copy_to_managed": config.copy_to_managed_check.isChecked(),
                "sign": config.sign_check.isChecked(),
                "sign_key": config.sign_key_combo.currentData(),
                "dwarfs": config.dwarfs_check.isChecked(),
            },
        }
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Project",
            os.path.expanduser("~"),
            PROJECT_FILE_FILTER,
        )
        if path:
            if not path.endswith(".niruviproject") and not path.endswith(".json"):
                path += ".niruviproject"
            try:
                with open(path, "w") as f:
                    json.dump(data, f, indent=2)
                self._current_project_path = path
                self.setWindowTitle(f"Build AppImage — {Path(path).name}")
                play_sound("success")
            except Exception as e:
                play_sound("error")
                QMessageBox.warning(self, "Save Failed", str(e))

    def _load_project(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Project",
            os.path.expanduser("~"),
            PROJECT_FILE_FILTER,
        )
        if not path:
            return
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception as e:
            play_sound("error")
            QMessageBox.warning(self, "Load Failed", f"Could not read project file:\n{e}")
            return

        proj = data.get("project", {})
        build = data.get("build", {})

        setup = self._setup_page()
        config = self._config_page()

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

        # Build config tab
        config.output_edit.setText(build.get("output_dir", os.path.expanduser("~/Applications")))
        config.copy_to_managed_check.setChecked(build.get("copy_to_managed", True))
        config.sign_check.setChecked(build.get("sign", False))
        config.dwarfs_check.setChecked(build.get("dwarfs", False))
        sign_key = build.get("sign_key", "")
        if sign_key:
            idx = config.sign_key_combo.findData(sign_key)
            if idx >= 0:
                config.sign_key_combo.setCurrentIndex(idx)

        self._current_project_path = path
        self.setWindowTitle(f"Build AppImage — {Path(path).name}")
