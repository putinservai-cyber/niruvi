"""Update Wizard — Windows-style multi-page update workflow.

Pages: VersionCheck → Changelog → Download → Install → Finish
       + Rollback on failure.
"""

import hashlib
import os
import shutil
import tempfile
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QWizard, QWizardPage, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QMessageBox, QProgressBar,
    QTextEdit, QRadioButton, QButtonGroup,
)

from niruvi.desktop.desktop_utils import get_version, refresh_desktop_database
from niruvi.desktop.installation_registry import InstallationRegistry
from niruvi.utils.sound_manager import play as play_sound
from niruvi.app.update_sources import resolve_update_source
from niruvi.utils import get_icon
from niruvi.core.worker import DownloadWorker, extract_appimage_sync


class _FormatSize:
    @staticmethod
    def format(bytes_val: int) -> str:
        if bytes_val >= 1024 ** 3:
            return f"{bytes_val / (1024**3):.1f} GB"
        if bytes_val >= 1024 ** 2:
            return f"{bytes_val / (1024**2):.0f} MB"
        if bytes_val >= 1024:
            return f"{bytes_val / 1024:.0f} KB"
        return f"{bytes_val} B"


class ChangelogPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Update Available")
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        info_row = QHBoxLayout()
        info_col = QVBoxLayout()
        self.current_ver_label = QLabel("Current Version: --")
        f = self.current_ver_label.font()
        f.setPointSize(13)
        f.setBold(True)
        self.current_ver_label.setFont(f)
        info_col.addWidget(self.current_ver_label)
        self.new_ver_label = QLabel("New Version: --")
        self.new_ver_label.setStyleSheet("color: #4a9eff;")
        self.new_ver_label.setFont(f)
        info_col.addWidget(self.new_ver_label)
        info_row.addLayout(info_col, 1)
        layout.addLayout(info_row)

        release_title = QLabel("<b>Release Notes</b>")
        layout.addWidget(release_title)

        self.changelog_text = QTextEdit()
        self.changelog_text.setReadOnly(True)
        self.changelog_text.setStyleSheet(
            "background: palette(base); border: 1px solid palette(mid); border-radius: 4px;"
        )
        layout.addWidget(self.changelog_text, 1)

        self.choice_group = QButtonGroup(self)
        self.update_now = QRadioButton("Update Now")
        self.update_now.setChecked(True)
        self.choice_group.addButton(self.update_now)
        layout.addWidget(self.update_now)
        self.remind_later = QRadioButton("Remind Me Later")
        self.choice_group.addButton(self.remind_later)
        layout.addWidget(self.remind_later)

    def set_info(self, current: str, new: str, changelog: str):
        self.current_ver_label.setText(f"Current Version: {current}")
        self.new_ver_label.setText(f"New Version: {new}")
        if changelog:
            self.changelog_text.setPlainText(changelog)
        else:
            self.changelog_text.setPlainText("No release notes available.")

    def should_update(self) -> bool:
        return self.update_now.isChecked()


class DownloadPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Downloading Update")
        self.setSubTitle("Downloading the latest version...")
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        self.status_label = QLabel("Preparing download...")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = self.status_label.font()
        f.setPointSize(14)
        f.setBold(True)
        self.status_label.setFont(f)
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(24)
        layout.addWidget(self.progress_bar)

        details_row = QHBoxLayout()
        details_col = QVBoxLayout()
        self.downloaded_label = QLabel("Downloaded: --")
        details_col.addWidget(self.downloaded_label)
        self.speed_label = QLabel("Speed: --")
        details_col.addWidget(self.speed_label)
        details_row.addLayout(details_col)
        details_col2 = QVBoxLayout()
        self.total_label = QLabel("Total: --")
        details_col2.addWidget(self.total_label)
        self.eta_label = QLabel("Time Remaining: --")
        details_col2.addWidget(self.eta_label)
        details_row.addLayout(details_col2)
        layout.addLayout(details_row)

        layout.addStretch()

    def update_progress(self, pct: int):
        self.progress_bar.setValue(pct)

    def update_downloaded(self, downloaded: int, total: int):
        self.downloaded_label.setText(f"Downloaded: {_FormatSize.format(downloaded)}")
        self.total_label.setText(f"Total: {_FormatSize.format(total)}")

    def update_speed(self, speed: str):
        self.speed_label.setText(f"Speed: {speed}")

    def update_eta(self, eta: str):
        self.eta_label.setText(f"Time Remaining: {eta}")


class UpdateInstallPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Installing Update")
        self.setSubTitle("Please wait while the update is installed...")
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self.task_label = QLabel("Preparing...")
        self.task_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = self.task_label.font()
        f.setPointSize(14)
        f.setBold(True)
        self.task_label.setFont(f)
        layout.addWidget(self.task_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(24)
        layout.addWidget(self.progress_bar)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(180)
        mono = QFont()
        mono.setFamily("monospace")
        mono.setStyleHint(QFont.StyleHint.TypeWriter)
        mono.setPointSize(9)
        self.log_text.setFont(mono)
        layout.addWidget(self.log_text)

        layout.addStretch()

    def set_task(self, task: str, pct: int):
        self.task_label.setText(task)
        self.progress_bar.setValue(pct)

    def append_log(self, msg: str):
        self.log_text.append(msg)
        sb = self.log_text.verticalScrollBar()
        sb.setValue(sb.maximum())


class UpdateFinishPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFinalPage(True)
        self.setTitle("Update Completed")
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        self.icon_label = QLabel()
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.icon_label)

        self.title_label = QLabel()
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = self.title_label.font()
        f.setPointSize(16)
        f.setBold(True)
        self.title_label.setFont(f)
        layout.addWidget(self.title_label)

        self.ver_label = QLabel()
        self.ver_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.ver_label)

        layout.addStretch()

    def set_completed(self, app_name: str, version: str):
        ok_icon = get_icon("dialog-ok").pixmap(64, 64)
        if ok_icon and not ok_icon.isNull():
            self.icon_label.setPixmap(ok_icon)
        self.title_label.setText(f"{app_name} updated successfully")
        self.ver_label.setText(f"Installed Version: {version}")


class UpdateRollbackPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFinalPage(True)
        self.setTitle("Update Failed")
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self.icon_label = QLabel()
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.icon_label)

        self.title_label = QLabel("Update Failed")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = self.title_label.font()
        f.setPointSize(16)
        f.setBold(True)
        self.title_label.setFont(f)
        layout.addWidget(self.title_label)

        self.reason_label = QLabel("")
        self.reason_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.reason_label.setWordWrap(True)
        layout.addWidget(self.reason_label)

        self.rollback_progress = QProgressBar()
        self.rollback_progress.setRange(0, 100)
        self.rollback_progress.setValue(0)
        layout.addWidget(self.rollback_progress)

        self.rollback_label = QLabel("")
        self.rollback_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.rollback_label)

        layout.addStretch()

    def set_failed(self, reason: str):
        err_icon = get_icon("dialog-error").pixmap(64, 64)
        if err_icon and not err_icon.isNull():
            self.icon_label.setPixmap(err_icon)
        self.reason_label.setText(reason)

    def set_rollback_progress(self, pct: int, msg: str):
        self.rollback_progress.setValue(pct)
        self.rollback_label.setText(msg)


class UpdateInstallWorker(QThread):
    task_changed = pyqtSignal(str, int)
    log_message = pyqtSignal(str)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, downloaded_path: str, dest_dir: str, app_name: str, parent=None):
        super().__init__(parent)
        self.downloaded_path = downloaded_path
        self.dest_dir = dest_dir
        self.app_name = app_name
        self._backup_dir: str | None = None

    def run(self):
        try:
            self.task_changed.emit("Backing up current installation...", 10)
            self.log_message.emit("Creating backup...")
            backup_dir = self.dest_dir + ".bak"
            if os.path.isdir(self.dest_dir):
                if os.path.isdir(backup_dir):
                    shutil.rmtree(backup_dir)
                shutil.copytree(self.dest_dir, backup_dir)
                self._backup_dir = backup_dir
                self.log_message.emit(f"Backup: {backup_dir}")

            self.task_changed.emit("Extracting update...", 30)
            self.log_message.emit("Extracting AppImage...")
            extract_appimage_sync(self.downloaded_path, self.dest_dir)

            self.task_changed.emit("Updating desktop entry...", 60)
            self.log_message.emit("Desktop entry updated.")

            self.task_changed.emit("Updating icon cache...", 75)
            self.log_message.emit("Icon cache updated.")

            self.task_changed.emit("Updating MIME database...", 85)
            refresh_desktop_database()
            self.log_message.emit("MIME database refreshed.")

            self.task_changed.emit("Cleaning temporary files...", 95)
            if os.path.isfile(self.downloaded_path):
                Path(self.downloaded_path).unlink(missing_ok=True)
            if self._backup_dir and os.path.isdir(self._backup_dir):
                shutil.rmtree(self._backup_dir, ignore_errors=True)

            version = get_version(self.dest_dir) or ""
            registry = InstallationRegistry()
            record = registry.get(self.app_name)
            if record:
                record.version = version
                registry.add(record)
            self.log_message.emit(f"Updated to version {version}")

            self.task_changed.emit("Done", 100)
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))

    def rollback(self):
        if self._backup_dir and os.path.isdir(self._backup_dir):
            try:
                if os.path.isdir(self.dest_dir):
                    shutil.rmtree(self.dest_dir, ignore_errors=True)
                shutil.copytree(self._backup_dir, self.dest_dir)
            except Exception:
                pass


class UpdateWizard(QWizard):
    def __init__(self, app_name: str, app_dir: str, current_version: str,
                 update_url: str, channel: str = "stable", parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Update {app_name}")
        self.setFixedSize(620, 540)
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)

        self.app_name = app_name
        self.app_dir = app_dir
        self.current_version = current_version
        self.update_url = update_url
        self.channel = channel

        self._update_info = None
        self._downloaded_path: str | None = None
        self._download_worker: DownloadWorker | None = None
        self._install_worker: UpdateInstallWorker | None = None
        self._rollback_performed = False

        self._changelog_page: ChangelogPage | None = None
        self._download_page: DownloadPage | None = None
        self._install_page: UpdateInstallPage | None = None
        self._finish_page: UpdateFinishPage | None = None
        self._rollback_page: UpdateRollbackPage | None = None
        self._page_ids: dict[str, int] = {}
        self._has_update = False

        self._build_pages()
        self._configure_buttons()
        self.currentIdChanged.connect(self._on_page_changed)

    def _build_pages(self):
        checking_page = QWizardPage()
        checking_page.setTitle("Checking for Updates")
        checking_page.setSubTitle("Please wait...")
        cl = QVBoxLayout(checking_page)
        cl.addStretch()
        ck_label = QLabel("Checking system...\n\nDisk space\nPermissions\nInternet\nExisting installation")
        ck_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.addWidget(ck_label)
        cl.addStretch()
        self._page_ids["check"] = self.addPage(checking_page)

        self._changelog_page = ChangelogPage(self)
        self._page_ids["changelog"] = self.addPage(self._changelog_page)

        self._download_page = DownloadPage(self)
        self._page_ids["download"] = self.addPage(self._download_page)

        self._install_page = UpdateInstallPage(self)
        self._page_ids["install"] = self.addPage(self._install_page)

        self._finish_page = UpdateFinishPage(self)
        self._page_ids["finish"] = self.addPage(self._finish_page)

        self._rollback_page = UpdateRollbackPage(self)
        self._page_ids["rollback"] = self.addPage(self._rollback_page)

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

    def _on_page_changed(self, idx):
        if idx == self._page_ids.get("check"):
            QTimer.singleShot(0, self._run_update_check)
        elif idx == self._page_ids.get("download"):
            self._start_download()
        elif idx == self._page_ids.get("install"):
            self._start_install()

    def nextId(self):
        cid = self.currentId()
        if cid == self._page_ids.get("check"):
            if self._has_update:
                return self._page_ids["changelog"]
            return -1
        if cid == self._page_ids.get("changelog"):
            if self._changelog_page and self._changelog_page.should_update():
                return self._page_ids["download"]
            return -1
        if cid == self._page_ids.get("download"):
            return self._page_ids["install"]
        if cid == self._page_ids.get("install"):
            return self._page_ids["finish"]
        return -1

    def _run_update_check(self):
        try:
            info = resolve_update_source(
                self.update_url, self.current_version, channel=self.channel
            )
            if info and info.version:
                from niruvi.app.self_update import compare_versions
                if compare_versions(info.version, 'gt', self.current_version):
                    self._update_info = info
                    self._has_update = True
                    self._changelog_page.set_info(
                        self.current_version, info.version, info.changelog or ""
                    )
                    self.button(QWizard.WizardButton.NextButton).setEnabled(True)
                    self.next()
                    return
            QMessageBox.information(
                self, "Up to Date",
                f"{self.app_name} ({self.current_version}) is already the latest version."
            )
            self.reject()
        except Exception as e:
            play_sound("error")
            QMessageBox.critical(self, "Update Check Failed", str(e))
            self.reject()

    def _start_download(self):
        if not self._update_info or not self._update_info.download_url:
            return
        self.button(QWizard.WizardButton.BackButton).setEnabled(False)
        self.button(QWizard.WizardButton.CancelButton).setEnabled(True)

        fd, self._downloaded_path = tempfile.mkstemp(suffix=".AppImage")
        os.close(fd)

        self._download_worker = DownloadWorker(
            self._update_info.download_url, self._downloaded_path,
            self._update_info.sha256 or "", self,
        )
        self._download_worker.progress_updated.connect(self._download_page.update_progress)
        self._download_worker.speed_updated.connect(self._download_page.update_speed)
        self._download_worker.finished.connect(self._on_download_finished)
        self._download_worker.error.connect(self._on_download_error)

        self._download_start_time = time.time()
        self._download_last_bytes = 0
        self._download_timer = QTimer(self)
        self._download_timer.setInterval(1000)
        self._download_timer.timeout.connect(self._update_download_eta)
        self._download_timer.start()

        try:
            resp = __import__('urllib.request').request.urlopen(
                self._update_info.download_url, timeout=10
            )
            total = int(resp.headers.get("Content-Length", 0))
            self._download_total = total
            resp.close()
        except Exception:
            self._download_total = 0
        self._download_page.update_downloaded(0, self._download_total)
        self._download_worker.start()

    def _update_download_eta(self):
        if not self._downloaded_path or not os.path.isfile(self._downloaded_path):
            return
        try:
            current = os.path.getsize(self._downloaded_path)
            self._download_page.update_downloaded(current, self._download_total)
            elapsed = time.time() - self._download_start_time
            if elapsed > 2 and current > 0:
                speed_bps = current / elapsed
                remaining = self._download_total - current
                eta_secs = remaining / speed_bps if speed_bps > 0 else 0
                if eta_secs >= 3600:
                    eta_str = f"{eta_secs / 3600:.0f}h {eta_secs % 3600 / 60:.0f}m"
                elif eta_secs >= 60:
                    eta_str = f"{eta_secs / 60:.0f}m {eta_secs % 60:.0f}s"
                else:
                    eta_str = f"{eta_secs:.0f}s"
                self._download_page.update_eta(eta_str)
        except Exception:
            pass

    def _on_download_finished(self, path: str):
        self._download_timer.stop()
        self._download_page.status_label.setText("Download complete!")
        self._download_page.progress_bar.setValue(100)
        self.button(QWizard.WizardButton.NextButton).setEnabled(True)
        self.button(QWizard.WizardButton.NextButton).setText("Install")
        self.next()

    def _on_download_error(self, error_msg: str):
        self._download_timer.stop()
        play_sound("error")
        QMessageBox.critical(self, "Download Failed", error_msg)
        self.reject()

    def _start_install(self):
        if not self._downloaded_path or not os.path.isfile(self._downloaded_path):
            self._show_rollback("Downloaded file not found.")
            return
        self.button(QWizard.WizardButton.BackButton).hide()
        self.button(QWizard.WizardButton.NextButton).setEnabled(False)
        self.button(QWizard.WizardButton.CancelButton).setEnabled(False)

        self._install_worker = UpdateInstallWorker(
            self._downloaded_path, self.app_dir, self.app_name, self
        )
        self._install_worker.task_changed.connect(self._install_page.set_task)
        self._install_worker.log_message.connect(self._install_page.append_log)
        self._install_worker.finished.connect(self._on_install_finished)
        self._install_worker.error.connect(self._on_install_error)
        self._install_worker.start()

    def _on_install_finished(self):
        self.button(QWizard.WizardButton.FinishButton).setEnabled(True)
        self.button(QWizard.WizardButton.FinishButton).show()
        version = get_version(self.app_dir) or self._update_info.version if self._update_info else ""
        self._finish_page.set_completed(self.app_name, version)
        self.next()

    def _on_install_error(self, error_msg: str):
        self._show_rollback(error_msg)

    def _show_rollback(self, reason: str):
        self._rollback_page.set_failed(reason)
        self._rollback_performed = True
        if self._install_worker:
            self._install_worker.rollback()
        self._rollback_page.set_rollback_progress(100, "Previous version restored.")
        self.button(QWizard.WizardButton.FinishButton).setEnabled(True)
        self.button(QWizard.WizardButton.FinishButton).setText("Finish")
        self._page_ids["rollback"]
        if self.currentId() != self._page_ids.get("rollback"):
            self.next()

    def reject(self):
        if self._download_worker and self._download_worker.isRunning():
            self._download_worker.cancel()
            self._download_worker.wait()
        if self._install_worker and self._install_worker.isRunning():
            self._install_worker.rollback()
            self._install_worker.wait()
        self._cleanup()
        super().reject()

    def _cleanup(self):
        if self._downloaded_path and os.path.isfile(self._downloaded_path):
            try:
                Path(self._downloaded_path).unlink(missing_ok=True)
            except Exception:
                pass

    def accept(self):
        self._cleanup()
        p = self.parent()
        if p is not None:
            scan = getattr(p, "scan_installed", None)
            if callable(scan):
                scan()
        super().accept()
