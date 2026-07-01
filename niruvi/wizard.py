import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from niruvi.utils import get_icon
from PyQt6.QtWidgets import (
    QWizard, QWizardPage, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QMessageBox, QLineEdit,
    QProgressBar, QCheckBox, QTextEdit,
)

from niruvi.settings import get_settings
from niruvi.worker import ExtractionWorker, _ensure_local, _is_removable_path
from niruvi.desktop_utils import (
    get_version, create_desktop_entry, create_desktop_shortcut,
    find_icon_in_appdir, parse_desktop_file_content, refresh_desktop_database,
)
from niruvi.appimage_assets import extract_metadata
from niruvi.appimage_metadata import AppImageMetadata
from niruvi.icon_utils import get_pixmap_from_file
from niruvi.installation_registry import InstallationRegistry, InstallationRecord
from niruvi.sound_manager import play as play_sound



class ScanPage(QWizardPage):
    """Pre-install validation page."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scan_result = {"risk_level": "safe", "warnings": [], "sha256": "", "size_mb": 0}
        self.setTitle("AppImage Info")
        self.setSubTitle("Validating the AppImage before installation.")
        layout = QVBoxLayout(self)

        self.scan_result_label = QLabel("Checking...")
        self.scan_result_label.setWordWrap(True)
        self.scan_result_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scan_result_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.scan_result_label)

        layout.addStretch()

    def isComplete(self):
        return True

    def set_scan_result(self, result):
        self._scan_result = result
        lines = [f"<b>Size:</b> {result.get('size_mb', 0):.1f} MB"]
        sha = result.get("sha256", "")[:16]
        if sha:
            lines.append(f"<br><b>SHA256:</b> {sha}...")
        arch = result.get("architecture", "")
        if arch:
            lines.append(f"<br><b>Architecture:</b> {arch}")
        fmt = result.get("format_check", {})
        if fmt.get("valid"):
            lines.append(f"<br><b>Format:</b> Valid ({fmt.get('fs_type', '?')})")
        if fmt.get("executable") is False:
            lines.append("<br><span style='color:orange;'>Not executable — will fix on install</span>")
        if not fmt.get("fuse_available"):
            lines.append("<br><span style='color:orange;'>FUSE unavailable</span>")
        warnings = result.get("warnings", [])
        if warnings:
            lines.append("<br><br><b>Notes:</b><br>")
            lines.extend(f"• {w}" for w in warnings[:8])
        if not warnings:
            lines.append("<br><br><span style='color:green;'>AppImage is ready to install.</span>")
        self.scan_result_label.setText("".join(lines))
        self.completeChanged.emit()

    def scan_result(self):
        return self._scan_result


class InstallWizard(QWizard):
    def __init__(self, appimage_path=None, parent=None, appimage_info=None, icon_data=None):
        super().__init__(parent)
        self.setFixedSize(600, 500)
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setStyleSheet("""
            QWizardPage { background: palette(window); }
            QLabel { font-size: 12px; }
            QLabel[heading="true"] { font-size: 14px; font-weight: bold; }
            QProgressBar {
                border: 1px solid palette(mid);
                border-radius: 6px;
                text-align: center;
                height: 22px;
                background: palette(window);
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #4a9eff, stop:1 #6cb4ff);
                border-radius: 5px;
            }
            QPushButton {
                padding: 6px 16px;
                border: 1px solid palette(mid);
                border-radius: 5px;
                background: palette(button);
                font-size: 12px;
            }
            QPushButton:hover {
                background: palette(light);
                border-color: palette(highlight);
            }
            QPushButton:pressed {
                background: palette(midlight);
            }
            QCheckBox {
                spacing: 8px;
                font-size: 12px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 3px;
                border: 1px solid palette(mid);
            }
            QCheckBox::indicator:checked {
                background: palette(highlight);
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

        self._scan_page_id = -1
        self._scan_page = None
        self._scan_result = None
        self._integration_page_id = -1
        self._progress_page_id = -1
        self._download_worker = None

        self._build_pages()
        self._configure_buttons()

        if appimage_path:
            self._select_file(appimage_path)

        self.currentIdChanged.connect(self._on_page_changed)

    def _build_pages(self):
        self.setWindowTitle("Install AppImage")

        p1 = QWizardPage()
        p1.setTitle("AppImage Information")
        p1.setSubTitle("Review the AppImage details and choose install location.")
        l1 = QVBoxLayout(p1)

        self.app_icon_label = QLabel()
        self.app_icon_label.setFixedSize(64, 64)
        self.app_name_label = QLabel("Name: --")
        self.app_path_label = QLabel("Source: --")
        self.app_size_label = QLabel("Size: --")
        self.app_comment_label = QLabel("")
        self.app_comment_label.setWordWrap(True)

        header = QHBoxLayout()
        header.addWidget(self.app_icon_label)
        info_col = QVBoxLayout()
        info_col.addWidget(self.app_name_label)
        info_col.addWidget(self.app_path_label)
        info_col.addWidget(self.app_size_label)
        info_col.addWidget(self.app_comment_label)
        header.addLayout(info_col, 1)
        l1.addLayout(header)

        dest_section = QHBoxLayout()
        dest_label = QLabel("Install to:")
        self.dest_dir_label = QLabel("--")
        self.dest_dir_label.setWordWrap(True)
        self.btn_change_dest = QPushButton(get_icon("folder-open"), "Change...")
        self.btn_change_dest.clicked.connect(self._on_change_dest)
        dest_section.addWidget(dest_label)
        dest_section.addWidget(self.dest_dir_label, 1)
        dest_section.addWidget(self.btn_change_dest)
        l1.addLayout(dest_section)

        l1.addStretch()
        self.addPage(p1)

        # Security Scan Page with gating
        self._scan_page = ScanPage(self)
        scan_page_id = self.addPage(self._scan_page)
        self._scan_page_id = scan_page_id

        p3 = QWizardPage()
        p3.setTitle("Integration & Isolation")
        p3.setSubTitle("Configure desktop integration and process isolation options.")
        l3 = QVBoxLayout(p3)
        l3.setSpacing(6)

        sec_title = QLabel("<b>Desktop Integration</b>")
        l3.addWidget(sec_title)

        self.cb_desktop_file = QCheckBox("Create desktop entry (show in application menu)")
        self.cb_desktop_file.setChecked(get_settings().get("create_desktop", True))
        self.cb_desktop_file.setToolTip(
            "Creates a .desktop file in ~/.local/share/applications/ so the app\n"
            "appears in your desktop environment's application launcher (GNOME, KDE, XFCE, etc.)"
        )
        l3.addWidget(self.cb_desktop_file)

        self.cb_desktop_shortcut = QCheckBox("Create desktop shortcut")
        self.cb_desktop_shortcut.setChecked(get_settings().get("create_shortcut", False))
        self.cb_desktop_shortcut.setToolTip(
            "Places a shortcut icon on your Desktop for quick launching.\n"
            "The icon will be installed to your system icon theme for best compatibility."
        )
        l3.addWidget(self.cb_desktop_shortcut)

        l3.addSpacing(8)
        sep2_title = QLabel("<b>Data Isolation</b>")
        l3.addWidget(sep2_title)

        self.cb_portable_home = QCheckBox("Portable home folder (.home)")
        self.cb_portable_home.setChecked(get_settings().get("portable_home", False))
        self.cb_portable_home.setToolTip(
            "Creates a .home folder next to the app directory where the app\n"
            "stores its user data, keeping it self-contained and portable."
        )
        l3.addWidget(self.cb_portable_home)

        self.cb_portable_config = QCheckBox("Portable config folder (.config)")
        self.cb_portable_config.setChecked(get_settings().get("portable_config", False))
        self.cb_portable_config.setToolTip(
            "Creates a .config folder next to the app directory where the app\n"
            "stores its configuration, keeping it self-contained and portable."
        )
        l3.addWidget(self.cb_portable_config)

        l3.addSpacing(8)
        sep3_title = QLabel("<b>Process Hardening</b>")
        l3.addWidget(sep3_title)

        self.cb_hardening = QCheckBox("Enable memory & process hardening")
        self.cb_hardening.setChecked(get_settings().get("sandbox_default_enabled", True))
        self.cb_hardening.setToolTip(
            "Applies rlimits, memory locking, ptrace disable, and malloc hardening."
        )
        l3.addWidget(self.cb_hardening)

        l3.addStretch()
        self._integration_page_id = self.addPage(p3)

        p4 = QWizardPage()
        p4.setTitle("Installing")
        p4.setSubTitle("Please wait while the AppImage is extracted...")
        l4 = QVBoxLayout(p4)

        self.progress_label = QLabel("Progress:")
        l4.addWidget(self.progress_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        l4.addWidget(self.progress_bar)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(200)
        mono_font = QFont()
        mono_font.setFamily("monospace")
        mono_font.setStyleHint(QFont.StyleHint.TypeWriter)
        mono_font.setPointSize(10)
        self.log_text.setFont(mono_font)
        l4.addWidget(self.log_text)
        self._progress_page_id = self.addPage(p4)

        p_success = QWizardPage()
        p_success.setFinalPage(True)
        l_success = QVBoxLayout(p_success)
        self.success_icon = QLabel()
        self.success_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.success_label = QLabel()
        self.success_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.success_label.setWordWrap(True)
        l_success.addStretch(1)
        l_success.addWidget(self.success_icon)
        l_success.addSpacing(10)
        l_success.addWidget(self.success_label)
        l_success.addStretch(1)
        self.addPage(p_success)

    def _configure_buttons(self):
        self.setButtonText(QWizard.WizardButton.CancelButton, "Cancel")
        self.button(QWizard.WizardButton.CancelButton).setIcon(get_icon("dialog-cancel"))
        self.setButtonText(QWizard.WizardButton.BackButton, "Back")
        self.button(QWizard.WizardButton.BackButton).setIcon(get_icon("go-previous"))
        self.setButtonText(QWizard.WizardButton.NextButton, "Next")
        self.button(QWizard.WizardButton.NextButton).setIcon(get_icon("go-next"))
        self.setButtonText(QWizard.WizardButton.FinishButton, "Finish")
        self.button(QWizard.WizardButton.FinishButton).setIcon(get_icon("dialog-ok"))
        self.button(QWizard.WizardButton.FinishButton).setEnabled(False)

    def _select_file(self, path: str):
        if _is_removable_path(path):
            self.appimage_path = _ensure_local(path, self.log_text.append)
        else:
            self.appimage_path = path
        base = os.path.splitext(os.path.basename(self.appimage_path))[0]
        self.app_name = base

        self.app_path_label.setText(f"Source: {path}")
        size_mb = os.path.getsize(self.appimage_path) / 1024 / 1024
        self.app_size_label.setText(f"Size: {size_mb:.1f} MB")

        if self._appimage_info.get("Name"):
            self.app_name = self._appimage_info["Name"]
            self.app_name_label.setText(f"Name: {self._appimage_info['Name']}")
            comment = self._appimage_info.get("Comment", "")
            if comment:
                self.app_comment_label.setText(f"Description: {comment}")
        else:
            self.app_name_label.setText(f"Name: {base}")

        if self._icon_data:
            from niruvi.icon_utils import get_pixmap_from_data
            pixmap = get_pixmap_from_data(self._icon_data, 64)
            if pixmap and not pixmap.isNull():
                self._icon_pixmap = pixmap
                self.app_icon_label.setPixmap(pixmap)
                self.success_icon.setPixmap(pixmap)

        if not self._icon_pixmap:
            self._load_appimage_metadata(path)

        if not self._icon_pixmap:
            generic = get_icon("package-x-generic", "application-x-archive").pixmap(64, 64)
            if generic and not generic.isNull():
                self.app_icon_label.setPixmap(generic)

        install_dir = get_settings()["install_dir"]
        self.dest_dir = os.path.join(install_dir, self.app_name)
        self.dest_dir_label.setText(self.dest_dir)

        if not self._appimage_info.get("Name"):
            self._load_appimage_metadata(path)

    def _cleanup_signals(self):
        self._stop_progress_animation()
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait()

    def set_appimage(self, path: str):
        self._select_file(path)

    def _load_appimage_metadata(self, path):
        self._architecture = ""
        try:
            meta = AppImageMetadata(self.appimage_path or path)
            self._architecture = meta.architecture
            self.app_size_label.setText(f"Size: {os.path.getsize(self.appimage_path or path) / 1024 / 1024:.1f} MB | Arch: {meta.architecture} | Type: Type{meta.type}")
        except Exception:
            pass

        if self._icon_pixmap:
            return

        try:
            with tempfile.TemporaryDirectory(prefix="aim-info-") as tmp:
                assets = extract_metadata(path, tmp)
                icon_path = assets.get("icon")
                if icon_path and os.path.isfile(icon_path):
                    from niruvi.icon_utils import get_pixmap_from_file
                    pixmap = get_pixmap_from_file(icon_path, 64)
                    if pixmap and not pixmap.isNull():
                        self._icon_pixmap = pixmap
                        self.app_icon_label.setPixmap(pixmap)
                        self.success_icon.setPixmap(pixmap)
        except Exception:
            pass

        try:
            with tempfile.TemporaryDirectory(prefix="aim-info-") as tmp:
                assets = extract_metadata(path, tmp)
                desktop_path = assets.get("desktop")
                if desktop_path:
                    content = Path(desktop_path).read_text(encoding='utf-8', errors='ignore')
                    self._desktop_info = parse_desktop_file_content(content)
                    name = self._desktop_info.get("Name", self.app_name)
                    self.app_name = name
                    self.app_name_label.setText(f"Name: {name}")
                    comment = self._desktop_info.get("Comment", "")
                    if comment:
                        self.app_comment_label.setText(f"Description: {comment}")

                icon_path = assets.get("icon")
                if icon_path:
                    try:
                        pixmap = get_pixmap_from_file(icon_path, 64)
                        if pixmap and not pixmap.isNull():
                            self._icon_pixmap = pixmap
                            self.app_icon_label.setPixmap(pixmap)
                            self.success_icon.setPixmap(pixmap)
                    except Exception:
                        pass
        except Exception:
            pass

    def _on_page_changed(self, idx):
        if idx == self._scan_page_id:
            self._run_security_scan()
        elif idx == self._integration_page_id:
            self._preset_sandbox()
        elif idx == self._progress_page_id:
            self._do_install()
            self.button(QWizard.WizardButton.FinishButton).hide()
        elif self.currentPage() and self.currentPage().isFinalPage():
            self.button(QWizard.WizardButton.BackButton).hide()

    def _preset_sandbox(self):
        rec = self._scan_result or {}
        rec_portable = rec.get("recommends_portable", False)
        self.cb_portable_home.setChecked(rec_portable or get_settings().get("portable_home", False))
        self.cb_portable_config.setChecked(rec_portable or get_settings().get("portable_config", False))
        self.cb_hardening.setChecked(get_settings().get("sandbox_default_enabled", True))

    def nextId(self):
        cid = self.currentId()
        if cid == 0:
            if get_settings().get("auto_scan_before_install", True):
                return self._scan_page_id
            return self._integration_page_id
        elif cid == self._scan_page_id:
            return self._integration_page_id
        elif cid == self._integration_page_id:
            return self._progress_page_id
        elif cid == self._progress_page_id:
            return self._progress_page_id + 1
        return -1

    def _run_security_scan(self):
        if not self.appimage_path or not os.path.isfile(self.appimage_path):
            self._scan_page.set_scan_result({
                "warnings": ["No AppImage file available"],
                "sha256": "", "size_mb": 0,
            })
            return
        result = {
            "risk_level": "safe",
            "warnings": [],
            "sha256": "",
            "size_mb": os.path.getsize(self.appimage_path) / (1024 * 1024),
        }
        try:
            meta = AppImageMetadata(self.appimage_path)
            result["sha256"] = meta.sha256
            result["architecture"] = meta.architecture
            result["format_check"] = {"valid": True, "fs_type": meta.fs_type, "executable": os.access(self.appimage_path, os.X_OK)}
        except Exception as e:
            result["warnings"].append(f"Could not parse AppImage: {e}")
        self._scan_result = result
        self._scan_page.set_scan_result(result)

    def validateCurrentPage(self):
        if self.currentId() == 0:
            if not self.appimage_path:
                play_sound("warning")
                QMessageBox.warning(self, "Error", "No AppImage file selected.")
                return False
            return True
        elif self.currentId() == self._scan_page_id:
            return True
        elif self.currentId() == self._integration_page_id:
            if not self.cb_desktop_file.isChecked() and not self.cb_desktop_shortcut.isChecked():
                reply = QMessageBox.question(
                    self,
                    "No Integration",
                    "No integration options selected. Proceed anyway?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return False
            dest = self.dest_dir
            if os.path.exists(dest):
                reply = QMessageBox.question(
                    self,
                    "Overwrite?",
                    f"Folder '{dest}' already exists.\n"
                    "Existing files will be backed up in case of failure.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return False
                self._create_backup(dest)
            return True
        return True

    def _on_change_dest(self):
        from PyQt6.QtWidgets import QFileDialog
        current = os.path.dirname(self.dest_dir) if self.dest_dir else get_settings()["install_dir"]
        new_dir = QFileDialog.getExistingDirectory(
            self, "Select Install Location", current
        )
        if new_dir:
            base = self.app_name or os.path.splitext(os.path.basename(self.appimage_path or ""))[0]
            self.dest_dir = os.path.join(new_dir, base)
            self.dest_dir_label.setText(self.dest_dir)

    def _create_backup(self, directory: str):
        if not os.path.isdir(directory):
            return
        import uuid
        safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', self.app_name or "unknown")[:64]
        backup_dir = os.path.join(tempfile.gettempdir(), f"aim-backup-{safe_name}-{uuid.uuid4().hex[:8]}")
        try:
            shutil.copytree(directory, backup_dir)
            self._backup_dir = backup_dir
            self.log_text.append(f"Backup created: {backup_dir}")
        except OSError as e:
            self.log_text.append(f"Warning: backup failed ({e})")

    def _restore_backup(self):
        if not self._backup_dir or not os.path.isdir(self._backup_dir):
            return
        try:
            if os.path.exists(self.dest_dir):
                shutil.rmtree(self.dest_dir)
            shutil.copytree(self._backup_dir, self.dest_dir, dirs_exist_ok=True)
            self.log_text.append("Previous installation restored from backup.")
        except OSError as e:
            self.log_text.append(f"Error restoring backup: {e}")

    def _cleanup_backup(self):
        if self._backup_dir and os.path.isdir(self._backup_dir):
            try:
                shutil.rmtree(self._backup_dir)
            except OSError:
                pass
        self._backup_dir = None

    def _validate_installation(self, dest_dir: str) -> tuple[bool, list[str]]:
        warnings: list[str] = []
        apprun = os.path.join(dest_dir, "AppRun")
        if not os.path.isfile(apprun):
            warnings.append("AppRun not found after extraction")
            return False, warnings
        if not os.access(apprun, os.X_OK):
            self.log_text.append("Setting AppRun executable...")
            try:
                os.chmod(apprun, 0o755)
            except OSError as e:
                warnings.append(f"Could not set executable: {e}")
                return True, warnings

        from niruvi.health_check import check_app_runnable, check_fuse_available
        diag = check_app_runnable(os.path.basename(dest_dir), dest_dir)
        if not diag["healthy"]:
            for issue in diag["issues"]:
                warnings.append(f"Diagnostic: {issue}")
        else:
            self.log_text.append("Pre-flight diagnostics passed.")

        if not check_fuse_available():
            warnings.append("FUSE is not available — AppImage may require extraction to run")
            self.log_text.append("Warning: FUSE not available.")

        if diag.get("warnings"):
            for w in diag["warnings"][:3]:
                self.log_text.append(f"Note: {w}")

        return True, warnings

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
        self._extraction_started = True

        self.button(QWizard.WizardButton.BackButton).setEnabled(False)
        self.button(QWizard.WizardButton.BackButton).hide()
        self.button(QWizard.WizardButton.NextButton).setEnabled(False)

        self.log_text.clear()
        self.log_text.append(f"Installing: {self.app_name}")
        self.log_text.append(f"Source: {self.appimage_path}")
        self.log_text.append(f"Destination: {self.dest_dir}")
        self._start_progress_animation()

        self.worker = ExtractionWorker(self.appimage_path, self.dest_dir, self.app_name)
        self.worker.extraction_finished.connect(self._on_extraction_finished)
        self.worker.extraction_error.connect(self._on_extraction_error)
        self.worker.progress_updated.connect(self._on_worker_progress)
        self.worker.log_message.connect(self._on_worker_log)
        self.worker.start()

    def _on_worker_progress(self, value: int):
        self._set_real_progress(value)

    def _on_worker_log(self, msg: str):
        self.log_text.append(msg)
        self.log_text.verticalScrollBar().setValue(
            self.log_text.verticalScrollBar().maximum()
        )

    def _on_extraction_finished(self, dest_dir: str, app_name: str):
        self._stop_progress_animation()
        self._set_real_progress(100)

        valid, diag_warnings = self._validate_installation(dest_dir)
        if not valid:
            self.log_text.append("Warning: extracted files may be incomplete.")
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
            self.log_text.append("Installation completed with warnings:")
            for w in diag_warnings:
                self.log_text.append(f"  • {w}")

        self.log_text.append("Installation complete!")
        play_sound("click")
        self.button(QWizard.WizardButton.NextButton).setEnabled(True)
        self.button(QWizard.WizardButton.FinishButton).setEnabled(True)
        self.button(QWizard.WizardButton.FinishButton).show()
        self.button(QWizard.WizardButton.CancelButton).setEnabled(False)

        try:
            version = get_version(dest_dir)
            metadata = {
                "version": version,
                "install_date": str(Path(dest_dir).stat().st_ctime),
            }
            if self._scan_result:
                metadata["scan_risk"] = self._scan_result.get("risk_level", "")
                metadata["scan_warnings"] = self._scan_result.get("warnings", [])
            meta_path = os.path.join(dest_dir, ".appimage-manager.json")
            with tempfile.NamedTemporaryFile(mode="w", dir=os.path.dirname(meta_path), delete=False, suffix=".tmp") as tf:
                json.dump(metadata, tf)
                tmp_path = tf.name
            os.replace(tmp_path, meta_path)
        except OSError as e:
            self.log_text.append(f"Warning: could not write metadata ({e})")

        desktop_file_path = None
        shortcut_path = None

        if self.cb_desktop_file.isChecked():
            try:
                desktop_file_path = create_desktop_entry(dest_dir, app_name, self)
                self.log_text.append(f"Desktop entry: {desktop_file_path or 'failed'}")
            except Exception as e:
                self.log_text.append(f"Desktop entry failed: {e}")

        if self.cb_desktop_shortcut.isChecked():
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
                shortcut_path = create_desktop_shortcut(
                    app_name, os.path.join(dest_dir, "AppRun"), icon_path
                )
                self.log_text.append(f"Desktop shortcut: {shortcut_path or 'failed'}")
            except Exception as e:
                self.log_text.append(f"Desktop shortcut failed: {e}")

        if self.cb_portable_home.isChecked():
            try:
                home_dir = dest_dir + ".home"
                Path(home_dir).mkdir(exist_ok=True)
                self.log_text.append(f"Portable home: {home_dir}")
            except OSError as e:
                self.log_text.append(f"Portable home failed: {e}")

        if self.cb_portable_config.isChecked():
            try:
                config_dir = dest_dir + ".config"
                Path(config_dir).mkdir(exist_ok=True)
                self.log_text.append(f"Portable config: {config_dir}")
            except OSError as e:
                self.log_text.append(f"Portable config failed: {e}")

        try:
            refresh_desktop_database()
        except Exception as e:
            self.log_text.append(f"Warning: desktop database refresh failed ({e})")

        try:
            import hashlib
            source_sha256 = ""
            if self.appimage_path and os.path.isfile(self.appimage_path):
                source_sha256 = hashlib.sha256(
                    Path(self.appimage_path).read_bytes()
                ).hexdigest()
            from niruvi.settings import get_settings
            _wiz_settings = get_settings()
            sandbox_config = {
                "enabled": self.cb_hardening.isChecked(),
                "hardening": self.cb_hardening.isChecked(),
                "portable_home": self.cb_portable_home.isChecked(),
                "portable_config": self.cb_portable_config.isChecked(),
                "backend": _wiz_settings.get("sandbox_default_backend", "shield"),
            }
            registry = InstallationRegistry()
            record = InstallationRecord(
                name=app_name,
                path=dest_dir,
                version=version,
                desktop_file=desktop_file_path or "",
                desktop_shortcut=shortcut_path or "",
                source_sha256=source_sha256,
                architecture=getattr(self, '_architecture', ''),
                sandbox_config=sandbox_config,
            )
            registry.add(record)
            self.log_text.append("Registered in installation database.")
        except Exception as e:
            self.log_text.append(f"Warning: registry update failed ({e})")

        self._cleanup_backup()

        self.progress_label.setText("Installation complete!")
        self.progress_bar.setValue(100)

        if self._icon_pixmap:
            self.success_icon.setPixmap(self._icon_pixmap)
        self.success_label.setText(f"<b>{app_name}</b> was installed successfully.")
        self.next()

    def _on_extraction_error(self, error_msg: str):
        self._stop_progress_animation()
        play_sound("error")
        self.log_text.append(f"ERROR: {error_msg}")
        if os.path.isdir(self.dest_dir):
            try:
                shutil.rmtree(self.dest_dir)
            except OSError:
                pass
        self._restore_backup()
        self._cleanup_backup()
        QMessageBox.critical(
            self, "Installation Error",
            f"Failed to install: {error_msg}\n\n"
            "The previous version has been restored."
        )
        self.reject()

    def _start_progress_animation(self):
        self._progress_tick = 0
        self._progress_real = -1
        self._progress_timer.start()

    def _stop_progress_animation(self):
        self._progress_timer.stop()

    def _on_progress_tick(self):
        self._progress_tick += 1
        display = max(self._simulated_value(), self._progress_real)
        self.progress_bar.setValue(min(display, 100))

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
        self.progress_bar.setValue(min(max(value, self._simulated_value()), 100))

    def done(self, result):
        self._cleanup_signals()
        super().done(result)

    def accept(self):
        p = self.parent()
        if p is not None:
            scan = getattr(p, "scan_installed", None)
            if callable(scan):
                scan()
        super().accept()

    def reject(self):
        if self.worker and self.worker.isRunning():
            reply = QMessageBox.question(
                self,
                "Cancel Installation?",
                "Installation is in progress. Cancel anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            self.worker.stop()
            self.worker.wait()
            if self.dest_dir and os.path.isdir(self.dest_dir):
                try:
                    shutil.rmtree(self.dest_dir)
                except OSError:
                    pass
        self._restore_backup()
        self._cleanup_backup()
        super().reject()
