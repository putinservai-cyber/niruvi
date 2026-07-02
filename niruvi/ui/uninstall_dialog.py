"""Uninstall Wizard — Windows-style multi-page uninstall workflow.

Pages: Welcome → RemoveOptions → Confirmation → Remove → Finish
       + Error handling for locked files.
"""

import os
import shutil
import subprocess
import traceback

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QWizard, QWizardPage, QVBoxLayout, QHBoxLayout,
    QLabel, QProgressBar, QPushButton, QCheckBox,
    QTextEdit, QMessageBox, QRadioButton, QButtonGroup,
    QGroupBox,
)

from niruvi.desktop.desktop_utils import (
    find_desktop_for_app, find_desktop_shortcut,
    refresh_desktop_database,
)
from niruvi.desktop.installation_registry import InstallationRegistry
from niruvi.utils.sound_manager import play as play_sound
from niruvi.utils import get_icon


def _unmount_if_fuse(path: str):
    if not os.path.ismount(path):
        return
    try:
        subprocess.run(["fusermount", "-u", path], capture_output=True, timeout=10)
    except Exception:
        pass


def _format_size(path: str) -> str:
    try:
        total = 0
        if os.path.isfile(path):
            total = os.path.getsize(path)
        elif os.path.isdir(path):
            for dirpath, _, filenames in os.walk(path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    try:
                        total += os.path.getsize(fp)
                    except OSError:
                        pass
        if total >= 1024 ** 3:
            return f"{total / (1024**3):.1f} GB"
        if total >= 1024 ** 2:
            return f"{total / (1024**2):.0f} MB"
        if total >= 1024:
            return f"{total / 1024:.0f} KB"
        return f"{total} B"
    except Exception:
        return "Unknown"


class UninstallWelcomePage(QWizardPage):
    def __init__(self, app_name: str, app_dir: str, parent=None):
        super().__init__(parent)
        self.setTitle(f"Uninstall {app_name}")
        self.setSubTitle("Choose an action for this application.")
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        info_row = QHBoxLayout()
        icon_lbl = QLabel()
        icon_lbl.setPixmap(get_icon("edit-delete").pixmap(48, 48))
        icon_lbl.setFixedSize(48, 48)
        info_row.addWidget(icon_lbl)
        info_col = QVBoxLayout()
        info_col.addWidget(QLabel(f"<b>{app_name}</b>"))
        info_col.addWidget(QLabel(f"Installation: <code>{app_dir}</code>"))
        info_col.addWidget(QLabel(f"Application Size: {_format_size(app_dir)}"))
        info_row.addLayout(info_col, 1)
        layout.addLayout(info_row)

        layout.addSpacing(12)

        self.choice_group = QButtonGroup(self)
        self.remove_radio = QRadioButton("Remove Application")
        self.remove_radio.setChecked(True)
        self.choice_group.addButton(self.remove_radio)
        layout.addWidget(self.remove_radio)

        self.repair_radio = QRadioButton("Repair Installation")
        self.choice_group.addButton(self.repair_radio)
        layout.addWidget(self.repair_radio)

        layout.addStretch()

    def get_choice(self) -> str:
        if self.remove_radio.isChecked():
            return "remove"
        if self.repair_radio.isChecked():
            return "repair"
        return "remove"


class RemoveOptionsPage(QWizardPage):
    def __init__(self, app_name: str, parent=None):
        super().__init__(parent)
        self.setTitle("Choose What to Remove")
        self.setSubTitle("Select which components to uninstall.")
        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        app_group = QGroupBox("Application")
        app_layout = QVBoxLayout(app_group)
        self.cb_app_files = QCheckBox("Application Files")
        self.cb_app_files.setChecked(True)
        self.cb_app_files.setEnabled(False)
        app_layout.addWidget(self.cb_app_files)
        layout.addWidget(app_group)

        integration_group = QGroupBox("Integration")
        int_layout = QVBoxLayout(integration_group)
        self.cb_desktop_entry = QCheckBox("Desktop Shortcut")
        self.cb_desktop_entry.setChecked(True)
        int_layout.addWidget(self.cb_desktop_entry)
        self.cb_start_menu = QCheckBox("Start Menu Entry")
        self.cb_start_menu.setChecked(True)
        int_layout.addWidget(self.cb_start_menu)
        self.cb_mime = QCheckBox("MIME Types")
        self.cb_mime.setChecked(True)
        int_layout.addWidget(self.cb_mime)
        self.cb_icons = QCheckBox("Icons")
        self.cb_icons.setChecked(True)
        int_layout.addWidget(self.cb_icons)
        self.cb_thumbnails = QCheckBox("Thumbnail Cache")
        self.cb_thumbnails.setChecked(True)
        int_layout.addWidget(self.cb_thumbnails)
        layout.addWidget(integration_group)

        user_group = QGroupBox("User Data")
        user_layout = QVBoxLayout(user_group)
        self.cb_settings = QCheckBox("User Settings")
        self.cb_settings.setChecked(False)
        user_layout.addWidget(self.cb_settings)
        self.cb_cache = QCheckBox("User Cache")
        self.cb_cache.setChecked(False)
        user_layout.addWidget(self.cb_cache)
        layout.addWidget(user_group)

        layout.addStretch()

    def get_checks(self) -> dict:
        return {
            "app_files": self.cb_app_files.isChecked(),
            "desktop_entry": self.cb_desktop_entry.isChecked(),
            "start_menu": self.cb_start_menu.isChecked(),
            "mime": self.cb_mime.isChecked(),
            "icons": self.cb_icons.isChecked(),
            "thumbnails": self.cb_thumbnails.isChecked(),
            "settings": self.cb_settings.isChecked(),
            "cache": self.cb_cache.isChecked(),
        }


class UninstallConfirmPage(QWizardPage):
    def __init__(self, app_name: str, app_dir: str, parent=None):
        super().__init__(parent)
        self.setTitle("Confirm Removal")
        self.setSubTitle("Review the items that will be removed.")
        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        self.items_label = QLabel()
        self.items_label.setWordWrap(True)
        layout.addWidget(self.items_label)

        self.size_label = QLabel()
        self.size_label.setStyleSheet("color: palette(disabled-text); font-size: 11px;")
        layout.addWidget(self.size_label)

        layout.addStretch()

    def set_items(self, checks: dict, app_dir: str):
        lines = ["<b>Items to remove:</b><ul>"]
        if checks.get("app_files"):
            lines.append(f"<li>Application: <code>{app_dir}</code></li>")
        if checks.get("desktop_entry"):
            lines.append("<li>Desktop Entry</li>")
        if checks.get("start_menu"):
            lines.append("<li>Start Menu Entry</li>")
        if checks.get("icons"):
            lines.append("<li>Icons</li>")
        if checks.get("mime"):
            lines.append("<li>MIME Types</li>")
        if checks.get("thumbnails"):
            lines.append("<li>Thumbnail Cache</li>")
        if checks.get("settings"):
            lines.append("<li>Settings <i>(optional)</i></li>")
        if checks.get("cache"):
            lines.append("<li>Cache <i>(optional)</i></li>")
        lines.append("</ul>")
        self.items_label.setText("".join(lines))
        self.size_label.setText(f"Estimated Space: {_format_size(app_dir)}")


class UninstallProgressPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Removing")
        self.setSubTitle("Please wait while the application is removed...")
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self.step_label = QLabel("Preparing...")
        self.step_label.setWordWrap(True)
        f = self.step_label.font()
        f.setPointSize(14)
        f.setBold(True)
        self.step_label.setFont(f)
        layout.addWidget(self.step_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(24)
        layout.addWidget(self.progress_bar)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(160)
        mono = QFont()
        mono.setFamily("monospace")
        mono.setStyleHint(QFont.StyleHint.TypeWriter)
        mono.setPointSize(9)
        self.log_text.setFont(mono)
        layout.addWidget(self.log_text)

        self.btn_toggle = QPushButton(get_icon("format-justify-left"), "Show Details")
        self.btn_toggle.setCheckable(True)
        self.btn_toggle.toggled.connect(
            lambda c: (
                self.log_text.setVisible(c),
                self.btn_toggle.setText("Hide Details" if c else "Show Details"),
            )
        )
        layout.addWidget(self.btn_toggle)

        layout.addStretch()

    def update_step(self, msg: str, pct: int):
        self.step_label.setText(msg)
        self.progress_bar.setValue(pct)
        self.log_text.append(msg)


class UninstallFinishPage(QWizardPage):
    def __init__(self, app_name: str, parent=None):
        super().__init__(parent)
        self.setFinalPage(True)
        self.setTitle("Uninstallation Completed")
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

        self.size_label = QLabel()
        self.size_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.size_label)

        self.restart_check = QCheckBox("Restart desktop shell")
        self.restart_check.setChecked(False)
        layout.addWidget(self.restart_check)

        self.logs_check = QCheckBox("Delete remaining logs")
        self.logs_check.setChecked(False)
        layout.addWidget(self.logs_check)

        layout.addStretch()

    def set_completed(self, app_name: str, app_dir: str):
        ok_icon = get_icon("dialog-ok").pixmap(64, 64)
        if ok_icon and not ok_icon.isNull():
            self.icon_label.setPixmap(ok_icon)
        self.title_label.setText(f"{app_name} has been removed")
        freed = _format_size(app_dir)
        self.size_label.setText(f"Freed Space: {freed}")


class UninstallWorker(QThread):
    step_changed = pyqtSignal(str, int)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, app_name, app_dir, checks, parent=None):
        super().__init__(parent)
        self.app_name = app_name
        self.app_dir = app_dir
        self.checks = checks

    def run(self):
        try:
            if self.checks.get("desktop_entry") or self.checks.get("start_menu"):
                self.step_changed.emit("Removing desktop entry...", 10)
                desktop_file = find_desktop_for_app(self.app_name)
                if desktop_file and os.path.exists(desktop_file):
                    os.remove(desktop_file)

            if self.checks.get("desktop_entry"):
                self.step_changed.emit("Removing desktop shortcut...", 15)
                shortcut = find_desktop_shortcut(self.app_name)
                if shortcut and os.path.exists(shortcut):
                    os.remove(shortcut)

            if self.checks.get("start_menu"):
                self.step_changed.emit("Removing start menu entry...", 20)
                uninstall_entry = os.path.expanduser(
                    f"~/.local/share/applications/uninstall-{self.app_name}.desktop"
                )
                if os.path.exists(uninstall_entry):
                    os.remove(uninstall_entry)

            if self.checks.get("mime"):
                self.step_changed.emit("Removing MIME types...", 25)

            if self.checks.get("icons"):
                self.step_changed.emit("Removing icons...", 30)
                icons_dir = os.path.expanduser("~/.local/share/icons")
                if os.path.isdir(icons_dir):
                    for root, _, files in os.walk(icons_dir):
                        for f in files:
                            if f.startswith(self.app_name + "."):
                                try:
                                    os.remove(os.path.join(root, f))
                                except OSError:
                                    pass

            if self.checks.get("thumbnails"):
                self.step_changed.emit("Removing thumbnail cache...", 35)
                thumb_dir = os.path.expanduser("~/.cache/thumbnails")
                if os.path.isdir(thumb_dir):
                    for root, _, files in os.walk(thumb_dir):
                        for f in files:
                            if self.app_name.lower() in f.lower():
                                try:
                                    os.remove(os.path.join(root, f))
                                except OSError:
                                    pass

            if self.checks.get("app_files"):
                self.step_changed.emit("Deleting application files...", 50)
                if os.path.isdir(self.app_dir):
                    try:
                        _unmount_if_fuse(self.app_dir)
                        shutil.rmtree(self.app_dir)
                    except OSError as e:
                        self.error.emit(
                            f"Unable to delete {self.app_dir}\n\n"
                            f"Reason: {e}\n\n"
                            "The application may still be running."
                        )
                        return

            if self.checks.get("settings"):
                home_dir = self.app_dir + ".home"
                if os.path.isdir(home_dir):
                    self.step_changed.emit("Removing settings...", 70)
                    try:
                        _unmount_if_fuse(home_dir)
                        shutil.rmtree(home_dir)
                    except OSError:
                        pass

            if self.checks.get("cache"):
                config_dir = self.app_dir + ".config"
                if os.path.isdir(config_dir):
                    self.step_changed.emit("Removing cache...", 75)
                    try:
                        _unmount_if_fuse(config_dir)
                        shutil.rmtree(config_dir)
                    except OSError:
                        pass

            prev_dir = self.app_dir + ".prev"
            if os.path.isdir(prev_dir):
                self.step_changed.emit("Removing rollback backup...", 80)
                _unmount_if_fuse(prev_dir)
                shutil.rmtree(prev_dir, ignore_errors=True)

            self.step_changed.emit("Updating installation registry...", 85)
            registry = InstallationRegistry()
            registry.remove(self.app_name)

            self.step_changed.emit("Refreshing desktop database...", 95)
            try:
                refresh_desktop_database()
            except Exception:
                pass

            self.step_changed.emit("Done", 100)
            self.finished.emit()
        except Exception as e:
            self.error.emit(f"{e}\n{traceback.format_exc()}")


class UninstallWizard(QWizard):
    def __init__(self, app_name, app_dir, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Uninstall {app_name}")
        self.setFixedSize(600, 500)
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setStyleSheet("""
            QWizardPage { background: palette(window); }
            QLabel { font-size: 12px; }
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

        self.app_name = app_name
        self.app_dir = app_dir
        self.worker: UninstallWorker | None = None
        self._uninstall_started = False
        self._redirected_to_repair = False

        self._build_pages()
        self._configure_buttons()
        self.currentIdChanged.connect(self._on_page_changed)

    def _build_pages(self):
        self._welcome_page = UninstallWelcomePage(self.app_name, self.app_dir, self)
        pid = self.addPage(self._welcome_page)
        self._page_welcome = pid

        self._options_page = RemoveOptionsPage(self.app_name, self)
        pid = self.addPage(self._options_page)
        self._page_options = pid

        self._confirm_page = UninstallConfirmPage(self.app_name, self.app_dir, self)
        pid = self.addPage(self._confirm_page)
        self._page_confirm = pid

        self._progress_page = UninstallProgressPage(self)
        pid = self.addPage(self._progress_page)
        self._page_progress = pid

        self._finish_page = UninstallFinishPage(self.app_name, self)
        pid = self.addPage(self._finish_page)
        self._page_finish = pid

        self.currentIdChanged.connect(self._on_page_changed)

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

    def nextId(self):
        cid = self.currentId()
        if cid == self._page_welcome:
            if self._welcome_page.get_choice() == "remove":
                self._redirected_to_repair = False
                return self._page_options
            self._redirected_to_repair = True
            return self._page_progress
        if cid == self._page_options:
            return self._page_confirm
        if cid == self._page_confirm:
            return self._page_progress
        if cid == self._page_progress:
            return self._page_finish
        return -1

    def initializePage(self, page_id):
        if page_id == self._page_confirm:
            checks = self._options_page.get_checks()
            self._confirm_page.set_items(checks, self.app_dir)
            self.setButtonText(QWizard.WizardButton.NextButton, "Uninstall")
            self.button(QWizard.WizardButton.NextButton).setIcon(get_icon("edit-delete"))
        elif page_id == self._page_progress:
            if self._redirected_to_repair:
                self._start_repair()
            else:
                self._start_uninstall()

    def _on_page_changed(self, idx):
        if idx == self._page_progress:
            self.button(QWizard.WizardButton.BackButton).hide()
            self.button(QWizard.WizardButton.NextButton).hide()
            self.button(QWizard.WizardButton.FinishButton).hide()
        elif self.currentPage() and self.currentPage().isFinalPage():
            self.button(QWizard.WizardButton.BackButton).hide()

    def _start_uninstall(self):
        if self._uninstall_started:
            return
        self._uninstall_started = True

        self.button(QWizard.WizardButton.BackButton).setEnabled(False)
        self.button(QWizard.WizardButton.CancelButton).setEnabled(False)

        checks = self._options_page.get_checks()
        self.worker = UninstallWorker(self.app_name, self.app_dir, checks, self)
        self.worker.step_changed.connect(self._progress_page.update_step)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _start_repair(self):
        self.button(QWizard.WizardButton.BackButton).setEnabled(False)
        self.button(QWizard.WizardButton.CancelButton).setEnabled(False)

        from niruvi.core.repair import repair_full
        self._progress_page.update_step("Repairing installation...", 10)
        report = repair_full(self.app_name, self.app_dir)

        steps = [
            ("Scanning files...", 30),
            ("Checking icons...", 50),
            ("Checking desktop entry...", 65),
            ("Checking permissions...", 80),
            ("Checking MIME...", 90),
            ("Repair complete", 100),
        ]
        for msg, pct in steps:
            self._progress_page.update_step(msg, pct)

        if report.all_succeeded:
            self._progress_page.update_step("Repair complete - all issues fixed.", 100)
        else:
            self._progress_page.update_step(
                f"Repair completed with {report.failure_count} issue(s).", 100
            )
        self._on_finished()

    def _on_finished(self):
        self._uninstall_started = False
        app_dir = self.app_dir
        if not self._redirected_to_repair:
            self._finish_page.set_completed(self.app_name, app_dir)
        else:
            self._finish_page.title_label.setText(f"{self.app_name} has been repaired")
            self._finish_page.size_label.hide()
            self._finish_page.restart_check.hide()
            self._finish_page.logs_check.hide()
        self.button(QWizard.WizardButton.FinishButton).setEnabled(True)
        self.button(QWizard.WizardButton.FinishButton).show()
        self.button(QWizard.WizardButton.CancelButton).hide()
        self.next()

    def _on_error(self, msg):
        play_sound("error")
        QMessageBox.critical(self, "Uninstall Error", msg.split("\n")[0])
        self.reject()

    def accept(self):
        p = self.parent()
        if p is not None:
            scan = getattr(p, "scan_installed", None)
            if callable(scan):
                scan()
        super().accept()
