import os
import shutil
import subprocess
import traceback

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from niruvi.utils import get_icon
from PyQt6.QtWidgets import (
    QWizard, QWizardPage, QVBoxLayout, QHBoxLayout,
    QLabel, QProgressBar, QPushButton, QCheckBox,
    QTextEdit, QMessageBox,
)

from niruvi.desktop_utils import (
    find_desktop_for_app, find_desktop_shortcut,
    refresh_desktop_database,
)
from niruvi.installation_registry import InstallationRegistry
from niruvi.sound_manager import play as play_sound


def _unmount_if_fuse(path: str):
    """Unmount a FUSE mount point if it looks like an AppImage mount."""
    if not os.path.ismount(path):
        return
    try:
        subprocess.run(
            ["fusermount", "-u", path],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass


class UninstallWorker(QThread):
    step_changed = pyqtSignal(str, int)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, app_name, app_dir, clean_home, clean_config, parent=None):
        super().__init__(parent)
        self.app_name = app_name
        self.app_dir = app_dir
        self.clean_home = clean_home
        self.clean_config = clean_config

    def run(self):
        try:
            self.step_changed.emit("Removing desktop entry...", 10)
            desktop_file = find_desktop_for_app(self.app_name)
            if desktop_file and os.path.exists(desktop_file):
                os.remove(desktop_file)

            self.step_changed.emit("Removing desktop shortcut...", 20)
            shortcut = find_desktop_shortcut(self.app_name)
            if shortcut and os.path.exists(shortcut):
                os.remove(shortcut)

            self.step_changed.emit("Removing uninstall entry...", 30)
            uninstall_entry = os.path.expanduser(
                f"~/.local/share/applications/uninstall-{self.app_name}.desktop"
            )
            if os.path.exists(uninstall_entry):
                os.remove(uninstall_entry)

            self.step_changed.emit("Cleaning legacy registry files...", 40)
            registry_path = os.path.expanduser(
                f"~/.local/share/niruvi-installed/{self.app_name}.path"
            )
            if os.path.exists(registry_path):
                os.remove(registry_path)

            self.step_changed.emit(f"Deleting {self.app_name} files...", 55)
            if os.path.isdir(self.app_dir):
                _unmount_if_fuse(self.app_dir)
                shutil.rmtree(self.app_dir)

            if self.clean_home:
                home_dir = self.app_dir + ".home"
                if os.path.isdir(home_dir):
                    shutil.rmtree(home_dir)

            if self.clean_config:
                config_dir = self.app_dir + ".config"
                if os.path.isdir(config_dir):
                    shutil.rmtree(config_dir)

            prev_dir = self.app_dir + ".prev"
            if os.path.isdir(prev_dir):
                self.step_changed.emit("Removing rollback backup...", 65)
                _unmount_if_fuse(prev_dir)
                shutil.rmtree(prev_dir)

            self.step_changed.emit("Cleaning icon cache...", 75)
            icons_dir = os.path.expanduser("~/.local/share/icons")
            if os.path.isdir(icons_dir):
                for root, _, files in os.walk(icons_dir):
                    for f in files:
                        if f.startswith(self.app_name + "."):
                            try:
                                os.remove(os.path.join(root, f))
                            except OSError:
                                pass

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


class StepPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Uninstalling")
        self.setSubTitle("Please wait while the app is being removed...")
        layout = QVBoxLayout(self)

        self.step_label = QLabel("Preparing...")
        self.step_label.setWordWrap(True)
        layout.addWidget(self.step_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(120)
        mono = QFont()
        mono.setFamily("monospace")
        mono.setStyleHint(QFont.StyleHint.TypeWriter)
        mono.setPointSize(10)
        self.log_text.setFont(mono)
        self.log_text.setVisible(False)
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

    def update_step(self, msg, pct):
        self.step_label.setText(msg)
        self.progress_bar.setValue(pct)
        self.log_text.append(f"[{pct}%] {msg}")


class DonePage(QWizardPage):
    def __init__(self, app_name, parent=None):
        super().__init__(parent)
        self.setFinalPage(True)
        self.setTitle("Uninstall Complete")
        layout = QVBoxLayout(self)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(get_icon("dialog-ok").pixmap(64, 64))
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(1)
        layout.addWidget(icon_lbl)
        layout.addSpacing(10)

        self.msg = QLabel(f"<b>{app_name}</b> has been uninstalled successfully.")
        self.msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.msg.setWordWrap(True)
        layout.addWidget(self.msg)
        layout.addStretch(1)

    def set_app_name(self, name):
        self.msg.setText(f"<b>{name}</b> has been uninstalled successfully.")


class UninstallWizard(QWizard):
    def __init__(self, app_name, app_dir, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Uninstall {app_name}")
        self.setFixedSize(600, 450)
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

        self._build_pages()
        self._configure_buttons()

    def _build_pages(self):
        confirm_page = QWizardPage()
        confirm_page.setTitle("Confirm Uninstall")
        confirm_page.setSubTitle(f"Remove <b>{self.app_name}</b> from your system?")
        cl = QVBoxLayout(confirm_page)

        icon_row = QHBoxLayout()
        icon_lbl = QLabel()
        icon_lbl.setPixmap(get_icon("edit-delete").pixmap(48, 48))
        icon_lbl.setFixedSize(48, 48)
        icon_row.addWidget(icon_lbl)
        info = QLabel(
            f"This will remove <b>{self.app_name}</b> and its files:<br>"
            f"<code>{self.app_dir}</code>"
        )
        info.setWordWrap(True)
        icon_row.addWidget(info, 1)
        cl.addLayout(icon_row)

        cl.addSpacing(12)

        self.cb_home = QCheckBox("Remove portable home folder (<code>.home</code>)")
        self.cb_home.setChecked(True)
        cl.addWidget(self.cb_home)

        self.cb_config = QCheckBox("Remove portable config folder (<code>.config</code>)")
        self.cb_config.setChecked(True)
        cl.addWidget(self.cb_config)

        cl.addStretch()
        self.addPage(confirm_page)

        self.step_page = StepPage(self)
        self.addPage(self.step_page)

        self.done_page = DonePage(self.app_name, self)
        self.addPage(self.done_page)

    def _configure_buttons(self):
        self.setButtonText(QWizard.WizardButton.CancelButton, "Cancel")
        self.button(QWizard.WizardButton.CancelButton).setIcon(get_icon("dialog-cancel"))
        self.setButtonText(QWizard.WizardButton.NextButton, "Uninstall")
        self.button(QWizard.WizardButton.NextButton).setIcon(get_icon("edit-delete"))
        self.setButtonText(QWizard.WizardButton.BackButton, "Back")
        self.button(QWizard.WizardButton.BackButton).setIcon(get_icon("go-previous"))
        self.setButtonText(QWizard.WizardButton.FinishButton, "Finish")
        self.button(QWizard.WizardButton.FinishButton).setIcon(get_icon("dialog-ok"))
        self.currentIdChanged.connect(self._on_page_changed)

    def initializePage(self, page_id):
        if page_id == 1:
            self._start_uninstall()

    def _on_page_changed(self, idx):
        if idx == 1:
            self.button(QWizard.WizardButton.BackButton).hide()
            self.button(QWizard.WizardButton.NextButton).hide()
            self.button(QWizard.WizardButton.FinishButton).hide()
        elif self.currentPage() and self.currentPage().isFinalPage():
            self.button(QWizard.WizardButton.BackButton).hide()
            self.button(QWizard.WizardButton.CancelButton).hide()

    def _start_uninstall(self):
        if getattr(self, '_uninstall_started', False):
            return
        self._uninstall_started = True

        self.button(QWizard.WizardButton.BackButton).setEnabled(False)
        self.button(QWizard.WizardButton.BackButton).hide()
        self.button(QWizard.WizardButton.CancelButton).setEnabled(False)
        self.button(QWizard.WizardButton.NextButton).setEnabled(False)
        self.button(QWizard.WizardButton.NextButton).hide()
        self.button(QWizard.WizardButton.FinishButton).hide()

        self.worker = UninstallWorker(
            self.app_name, self.app_dir,
            self.cb_home.isChecked(), self.cb_config.isChecked(),
            self,
        )
        self.worker.step_changed.connect(self.step_page.update_step)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _on_finished(self):
        self._uninstall_started = False
        self.button(QWizard.WizardButton.FinishButton).show()
        self.next()

    def _on_error(self, msg):
        play_sound("error")
        QMessageBox.critical(self, "Uninstall Error", msg.split("\n")[0])
        self.reject()
