#!/usr/bin/env python3
"""Standalone PyQt6 self-installer wizard for self-installing AppImages.

Injected into the AppImage's .niruvi-install/ directory during build.
Runs via system Python3 if PyQt6 is available.

Usage:
  python3 this_script.py --install
  python3 this_script.py --uninstall
  python3 this_script.py --update
  python3 this_script.py --check-updates
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication, QWizard, QWizardPage, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QFileDialog, QProgressBar,
    QCheckBox, QMessageBox, QTextBrowser, QTextEdit,
)
from PyQt6.QtGui import QIcon


def _fix_qt_platform_path():
    """Ensure Qt can find its platform plugins when running from an AppImage.

    The AppImage runtime may set QT_QPA_PLATFORM_PLUGIN_PATH to an empty
    or non-existent path, causing QApplication creation to fail with
    "Could not find the Qt platform plugin". We fix this by:

    1. Cleaning LD_LIBRARY_PATH so bundled Qt5 libs don't shadow system Qt6
    2. Pointing QT_QPA_PLATFORM_PLUGIN_PATH at the system Qt6 plugin dir
    """
    # Remove AppImage bundle paths from LD_LIBRARY_PATH so system
    # PyQt6 finds system Qt6 libraries, not bundled Qt5 ones
    old = os.environ.get("LD_LIBRARY_PATH", "")
    if old:
        cleaned = []
        for p in old.split(":"):
            if not p or not p.startswith("/tmp/.mount_"):
                cleaned.append(p)
        if cleaned:
            os.environ["LD_LIBRARY_PATH"] = ":".join(cleaned)
        else:
            os.environ.pop("LD_LIBRARY_PATH", None)

    cur = os.environ.get("QT_QPA_PLATFORM_PLUGIN_PATH", "")
    if cur and os.path.isdir(cur):
        return
    candidates = [
        "/usr/lib64/qt6/plugins",
        "/usr/lib/x86_64-linux-gnu/qt6/plugins",
        "/usr/lib64/qt6/plugins/platforms/..",
    ]
    for p in candidates:
        platforms = os.path.join(p, "platforms")
        if os.path.isdir(platforms) and any(
            f.startswith("libq") for f in os.listdir(platforms)
            if f.endswith(".so")
        ):
            os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = p
            return
    os.environ.pop("QT_QPA_PLATFORM_PLUGIN_PATH", None)

def _theme_icon(name):
    icon = QIcon.fromTheme(name)
    if not icon.isNull():
        return icon
    return QIcon()

SCRIPT_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = SCRIPT_DIR / "config.json"
LICENSE_PATH = SCRIPT_DIR / "license.txt"
COMPONENTS_PATH = SCRIPT_DIR / "components.cfg"
PRE_INSTALL_PATH = SCRIPT_DIR / "pre-install.sh"
POST_INSTALL_PATH = SCRIPT_DIR / "post-install.sh"


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def default_install_dir(config):
    return os.path.expanduser(config.get("install_dir", "~/Applications"))


def installed_dir(config):
    return os.path.join(default_install_dir(config), config["app_name"])


def marker_file(inst_dir):
    return os.path.join(inst_dir, ".installed")


def meta_file(inst_dir):
    return os.path.join(inst_dir, ".appimage-manager.json")


def is_installed(config):
    return os.path.isfile(marker_file(installed_dir(config)))


def installed_version(config):
    d = installed_dir(config)
    m = meta_file(d)
    if os.path.isfile(m):
        try:
            with open(m) as f:
                data = json.load(f)
            return data.get("version", "")
        except Exception:
            return ""
    return ""


# ── Worker ──


class InstallWorker(QThread):
    progress = pyqtSignal(int)
    log = pyqtSignal(str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, config, dest_dir, self_appimage):
        super().__init__()
        self.config = config
        self.dest = dest_dir
        self.self_appimage = self_appimage
        self._stop = False
        self._backup = None

    def stop(self):
        self._stop = True

    @staticmethod
    def _is_type1_appimage(path: str) -> bool:
        try:
            with open(path, "rb") as f:
                magic = f.read(12)
            if magic[:4] != b"\x7fELF":
                return False
            return magic[8:10] != b"AI"  # Type2 has "AI" at offset 8
        except Exception:
            return False

    def _extract_appimage(self):
        if self._is_type1_appimage(self.self_appimage):
            self.log.emit("Detected Type1 AppImage — using unsquashfs...")
            proc = subprocess.Popen(
                ["unsquashfs", "-d", "squashfs-root", "-force", self.self_appimage],
                cwd=self.dest,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
        else:
            self.log.emit("Extracting AppImage...")
            proc = subprocess.Popen(
                [self.self_appimage, "--appimage-extract"],
                cwd=self.dest,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
        for line in proc.stdout or []:
            self.log.emit(line.strip())
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError("Extraction failed")
        self.progress.emit(60)

    def run(self):
        try:
            self.log.emit(f"Installing to {self.dest}...")
            self.progress.emit(5)

            if self.config.get("enable_rollback", True) and os.path.isdir(self.dest):
                backup = tempfile.mkdtemp(prefix="aim-rollback-")
                for item in os.listdir(self.dest):
                    shutil.move(os.path.join(self.dest, item), backup)
                self._backup = backup
                self.log.emit("Backed up existing install")

            os.makedirs(self.dest, exist_ok=True)
            self.progress.emit(15)

            if self._stop:
                self.error.emit("Cancelled")
                return

            pre = str(PRE_INSTALL_PATH)
            if PRE_INSTALL_PATH.exists():
                self.log.emit("Running pre-install script...")
                subprocess.run(["/bin/bash", pre], cwd=self.dest, check=True)
                self.progress.emit(20)

            self._extract_appimage()

            srcdir = os.path.join(self.dest, "squashfs-root")
            if os.path.isdir(srcdir):
                for item in os.listdir(srcdir):
                    s = os.path.join(srcdir, item)
                    d = os.path.join(self.dest, item)
                    if os.path.exists(d):
                        if os.path.isdir(d):
                            shutil.rmtree(d)
                        else:
                            os.unlink(d)
                    shutil.move(s, d)
                os.rmdir(srcdir)
            self.progress.emit(75)

            Path(marker_file(self.dest)).write_text("")

            # Restore real application launcher (override self-installer AppRun)
            backup = os.path.join(self.dest, ".niruvi-install", "apprun-backup.sh")
            apprun_path = os.path.join(self.dest, "AppRun")
            if os.path.isfile(backup):
                shutil.copy2(backup, apprun_path)
                os.chmod(apprun_path, 0o755)
                self.log.emit("Restored real application launcher")
                # Validate restored AppRun
                import re as _re
                try:
                    with open(apprun_path) as _f:
                        _content = _f.read()
                    _m = _re.search(r'exec\s+"?\$HERE/([^"\s]+)', _content)
                    if _m:
                        _target = os.path.join(self.dest, _m.group(1))
                        if not os.path.exists(_target):
                            self.log.emit(f"Warning: AppRun target not found: {_target}")
                except Exception:
                    pass

            version = self.config.get("app_version", "1.0.0")
            meta = {"version": version, "install_date": str(int(os.path.getctime(self.self_appimage)))}
            with open(meta_file(self.dest), "w") as f:
                json.dump(meta, f, indent=2)
            self.log.emit(f"Installed version {version}")
            self.progress.emit(85)

            if COMPONENTS_PATH.exists():
                shutil.copy2(str(COMPONENTS_PATH), os.path.join(self.dest, ".niruvi-components.cfg"))

            post = str(POST_INSTALL_PATH)
            if POST_INSTALL_PATH.exists():
                self.log.emit("Running post-install script...")
                subprocess.run(["/bin/bash", post], cwd=self.dest, check=True)
                self.progress.emit(90)

            self._install_desktop()
            self.progress.emit(90)

            self._register_in_niruvi()
            self.progress.emit(95)

            self._refresh_desktop_db()
            self.progress.emit(100)
            self.log.emit("Installation complete!")
            self.finished.emit(self.dest)

        except Exception as e:
            self.error.emit(str(e))

    def _install_desktop(self):
        conf = self.config
        app_name = conf["app_name"]
        desktop_path = None
        for root, _, files in os.walk(self.dest):
            for f in files:
                if f.endswith(".desktop"):
                    desktop_path = os.path.join(root, f)
                    break
            if desktop_path:
                break
        if desktop_path:
            desktop_dir = os.path.expanduser("~/.local/share/applications")
            os.makedirs(desktop_dir, exist_ok=True)
            dest_desktop = os.path.join(desktop_dir, f"{app_name}.desktop")
            # Fix Exec line to point to the installed AppRun
            with open(desktop_path) as f:
                desktop_content = f.read()
            lines = desktop_content.splitlines(keepends=True)
            fixed = []
            for line in lines:
                if line.startswith("Exec="):
                    fixed.append(f"Exec={os.path.join(self.dest, 'AppRun')} %F\n")
                else:
                    fixed.append(line)
            with open(dest_desktop, "w") as f:
                f.writelines(fixed)
            self.log.emit(f"Desktop entry: {app_name}.desktop")

        icon_name = None
        if desktop_path:
            with open(desktop_path) as f:
                for line in f:
                    if line.startswith("Icon="):
                        icon_name = line.split("=", 1)[1].strip()
                        break
        if icon_name:
            for ext in (".png", ".svg", ".xpm"):
                candidates = list(Path(self.dest).rglob(f"{icon_name}{ext}"))
                if not candidates:
                    candidates = list(Path(self.dest).rglob(f"*{ext}"))
                if candidates:
                    icon_dir = os.path.expanduser("~/.local/share/icons/hicolor/256x256/apps")
                    os.makedirs(icon_dir, exist_ok=True)
                    shutil.copy2(str(candidates[0]), os.path.join(icon_dir, f"{app_name}{ext}"))
                    self.log.emit(f"Icon installed: {app_name}{ext}")
                    break

        uninstall_desktop = os.path.expanduser(f"~/.local/share/applications/{app_name}.uninstall.desktop")
        local_uninstall = os.path.join(self.dest, ".niruvi-uninstall.sh")
        shipped = os.path.join(self.dest, ".niruvi-install", "uninstall.sh")
        if os.path.isfile(shipped):
            shutil.copy2(shipped, local_uninstall)
            os.chmod(local_uninstall, 0o755)
        uninstall_exec = local_uninstall if os.path.isfile(local_uninstall) else shutil.which("xdg-open") or "true"
        uninstall_content = (
            "[Desktop Entry]\n"
            f"Name=Uninstall {app_name}\n"
            f"Exec={uninstall_exec}\n"
            "Icon=edit-delete\n"
            "Type=Application\n"
            "Categories=Utility;\n"
        )
        with open(uninstall_desktop, "w") as f:
            f.write(uninstall_content)

    def _register_in_niruvi(self):
        try:
            app_name = self.config.get("app_name", "")
            if not app_name:
                return
            install_dir = self.dest
            version = self.config.get("app_version", "")
            desktop_file = os.path.expanduser(f"~/.local/share/applications/{app_name}.desktop")
            if not os.path.isfile(desktop_file):
                desktop_file = ""
            data_dir = os.environ.get(
                "NIRUVI_DATA_DIR",
                os.path.expanduser("~/.config/niruvi"),
            )
            registry_path = os.path.join(data_dir, "registry.json")
            os.makedirs(data_dir, exist_ok=True)
            sha256 = ""
            if self.self_appimage and os.path.isfile(self.self_appimage):
                sha256 = hashlib.sha256(
                    open(self.self_appimage, "rb").read(65536)
                ).hexdigest()
            record = {
                "name": app_name,
                "path": install_dir,
                "version": version,
                "install_date": datetime.now().isoformat(),
                "install_type": "self-install",
                "source_sha256": sha256,
                "desktop_file": desktop_file,
                "desktop_shortcut": desktop_file,
                "update_url": self.config.get("updater_url", ""),
                "architecture": "",
                "display_name_override": self.config.get("brand_name", ""),
                "custom_icon_path": "",
                "env_vars": {},
                "run_args": "",
                "auto_update": False,
                "update_channel": "stable",
                "sandbox_config": {},
            }
            existing = []
            if os.path.isfile(registry_path):
                try:
                    with open(registry_path) as f:
                        existing = json.load(f)
                except (json.JSONDecodeError, OSError):
                    existing = []
            existing = [r for r in existing if r.get("name") != app_name]
            existing.append(record)
            tmp = registry_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(existing, f, indent=2)
            os.replace(tmp, registry_path)
            self.log.emit(f"Registered {app_name} in Niruvi ({install_dir})")
        except Exception as e:
            self.log.emit(f"Note: could not register in Niruvi: {e}")

    def _refresh_desktop_db(self):
        for cmd in ("update-desktop-database", "gtk-update-icon-cache"):
            try:
                subprocess.run(
                    [cmd, os.path.expanduser("~/.local/share/applications")],
                    capture_output=True, timeout=30,
                )
            except Exception:
                pass


class UninstallWorker(QThread):
    progress = pyqtSignal(int)
    log = pyqtSignal(str)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, config):
        super().__init__()
        self.config = config

    @staticmethod
    def _unregister_from_niruvi(app_name=""):
        try:
            if not app_name:
                return
            data_dir = os.environ.get(
                "NIRUVI_DATA_DIR",
                os.path.expanduser("~/.config/niruvi"),
            )
            registry_path = os.path.join(data_dir, "registry.json")
            if not os.path.isfile(registry_path):
                return
            with open(registry_path) as f:
                records = json.load(f)
            before = len(records)
            records = [r for r in records if r.get("name") != app_name]
            if len(records) == before:
                return
            tmp = registry_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(records, f, indent=2)
            os.replace(tmp, registry_path)
        except Exception:
            pass

    def run(self):
        try:
            conf = self.config
            app_name = conf["app_name"]
            inst_dir = installed_dir(conf)
            self.log.emit(f"Removing {app_name}...")
            self.progress.emit(20)

            desktop = os.path.expanduser(f"~/.local/share/applications/{app_name}.desktop")
            if os.path.isfile(desktop):
                os.unlink(desktop)

            uninst = os.path.expanduser(f"~/.local/share/applications/{app_name}.uninstall.desktop")
            if os.path.isfile(uninst):
                os.unlink(uninst)

            icon_home = os.path.expanduser("~/.local/share/icons/hicolor")
            if os.path.isdir(icon_home):
                for root, _, files in os.walk(icon_home):
                    for f in files:
                        if app_name in f:
                            os.unlink(os.path.join(root, f))
            self.progress.emit(50)

            if os.path.isdir(inst_dir):
                shutil.rmtree(inst_dir)
            self.progress.emit(80)

            self._unregister_from_niruvi(conf["app_name"])

            for cmd in ("update-desktop-database", "gtk-update-icon-cache"):
                try:
                    subprocess.run(
                        [cmd, os.path.expanduser("~/.local/share/applications")],
                        capture_output=True, timeout=30,
                    )
                except Exception:
                    pass
            self.progress.emit(100)
            self.log.emit("Uninstall complete")
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


class UpdateWorker(QThread):
    progress = pyqtSignal(int)
    log = pyqtSignal(str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    update_available = pyqtSignal(dict)
    no_update = pyqtSignal()

    def __init__(self, config):
        super().__init__()
        self.config = config

    def run(self):
        try:
            url = self.config.get("updater_url", "")
            if not url:
                self.no_update.emit()
                return
            self.log.emit("Checking for updates...")
            self.progress.emit(10)
            req = urllib.request.Request(url, headers={"User-Agent": "Niruvi-Updater/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                manifest = json.loads(resp.read().decode())
            remote_ver = manifest.get("version", "")
            current_ver = installed_version(self.config)
            if remote_ver and remote_ver != current_ver:
                self.log.emit(f"Update: {current_ver} -> {remote_ver}")
                self.progress.emit(30)
                self.update_available.emit(manifest)
            else:
                self.log.emit("Already up to date")
                self.no_update.emit()
        except Exception as e:
            self.error.emit(str(e))

    def download_and_install(self, manifest, self_appimage):
        try:
            self.log.emit("Downloading update...")
            self.progress.emit(40)
            download_url = manifest.get("download_url", "")
            if not download_url:
                self.error.emit("No download URL")
                return
            tmp = tempfile.NamedTemporaryFile(suffix=".AppImage", delete=False)
            tmp_path = tmp.name
            tmp.close()
            with urllib.request.urlopen(download_url, timeout=120) as resp:
                with open(tmp_path, "wb") as f:
                    shutil.copyfileobj(resp, f)
            os.chmod(tmp_path, 0o755)
            self.log.emit("Downloaded")
            self.progress.emit(70)

            expected_sha = manifest.get("sha256", "")
            if expected_sha:
                import hashlib
                actual = hashlib.sha256(open(tmp_path, "rb").read()).hexdigest()
                if actual != expected_sha:
                    os.unlink(tmp_path)
                    self.error.emit("SHA256 mismatch")
                    return
                self.log.emit("SHA256 verified")

            inst_dir = installed_dir(self.config)
            backup_dir = None
            if os.path.isdir(inst_dir):
                backup_dir = inst_dir + ".backup"
                if os.path.exists(backup_dir):
                    shutil.rmtree(backup_dir)
                os.rename(inst_dir, backup_dir)

            os.makedirs(inst_dir, exist_ok=True)

            self.log.emit("Extracting update...")
            extracted_ok = False
            try:
                proc = subprocess.Popen(
                    [tmp_path, "--appimage-extract"],
                    cwd=inst_dir,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                )
                for line in proc.stdout or []:
                    self.log.emit(line.strip())
                proc.wait()
                if proc.returncode != 0:
                    raise RuntimeError("Extraction failed")
                extracted_ok = True
            finally:
                if not extracted_ok and backup_dir and os.path.isdir(backup_dir):
                    if os.path.isdir(inst_dir):
                        shutil.rmtree(inst_dir)
                    os.rename(backup_dir, inst_dir)

            srcdir = os.path.join(inst_dir, "squashfs-root")
            if os.path.isdir(srcdir):
                for item in os.listdir(srcdir):
                    s = os.path.join(srcdir, item)
                    d = os.path.join(inst_dir, item)
                    if os.path.exists(d):
                        if os.path.isdir(d):
                            shutil.rmtree(d)
                        else:
                            os.unlink(d)
                    shutil.move(s, d)
                os.rmdir(srcdir)

            if backup_dir and os.path.isdir(backup_dir):
                shutil.rmtree(backup_dir, ignore_errors=True)

            version = manifest.get("version", "1.0.0")
            meta = {"version": version, "install_date": str(int(os.path.getctime(tmp_path)))}
            with open(meta_file(inst_dir), "w") as f:
                json.dump(meta, f, indent=2)
            os.unlink(tmp_path)
            self.progress.emit(100)
            self.finished.emit(inst_dir)
        except Exception as e:
            self.error.emit(str(e))


# ── Wizard Pages ──


class WelcomePage(QWizardPage):
    def __init__(self, config):
        super().__init__()
        self.setTitle("Welcome")
        brand = config.get("brand_name") or config["app_name"]
        msg = config.get("welcome_message", "")
        self.setSubTitle(f"Welcome to the {brand} Setup Wizard.")
        layout = QVBoxLayout(self)
        welcome = QLabel(
            f"<h2>{brand}</h2>"
            f"<p>{msg or 'This wizard will guide you through the installation.'}</p>"
        )
        welcome.setWordWrap(True)
        layout.addWidget(welcome)
        info = QLabel(
            f"<b>Application:</b> {config['app_name']}<br>"
            f"<b>Version:</b> {config.get('app_version', '?')}<br>"
            f"<b>Install to:</b> {default_install_dir(config)}"
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        layout.addStretch()


class LicensePage(QWizardPage):
    def __init__(self, config):
        super().__init__()
        self.setTitle("License Agreement")
        self.setSubTitle("Please review the license terms before installing.")
        self._accepted = False
        layout = QVBoxLayout(self)
        self.text = QTextBrowser()
        self.text.setReadOnly(True)
        content = LICENSE_PATH.read_text() if LICENSE_PATH.exists() else "No license file provided."
        self.text.setText(content)
        layout.addWidget(self.text, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.accept_btn = QPushButton(_theme_icon("dialog-ok-apply"), "Accept")
        self.accept_btn.clicked.connect(self._on_accept)
        btn_row.addWidget(self.accept_btn)
        self.decline_btn = QPushButton(_theme_icon("dialog-cancel"), "Decline")
        self.decline_btn.clicked.connect(self._on_decline)
        btn_row.addWidget(self.decline_btn)
        layout.addLayout(btn_row)

    def _on_accept(self):
        self._accepted = True
        self.completeChanged.emit()

    def _on_decline(self):
        self._accepted = False
        self.completeChanged.emit()

    def isComplete(self):
        return self._accepted


class DirectoryPage(QWizardPage):
    def __init__(self, config):
        super().__init__()
        self.setTitle("Installation Directory")
        self.setSubTitle("Choose where to install the application.")
        self._config = config
        layout = QVBoxLayout(self)
        self.dir_edit = QLineEdit(installed_dir(config))
        row = QHBoxLayout()
        row.addWidget(self.dir_edit, 1)
        btn = QPushButton("Browse...")
        btn.clicked.connect(self._browse)
        row.addWidget(btn)
        layout.addLayout(row)
        layout.addStretch()

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Select install directory", self.dir_edit.text())
        if d:
            self.dir_edit.setText(os.path.join(d, self._config["app_name"]))

    def installDir(self):
        return self.dir_edit.text()


class ComponentsPage(QWizardPage):
    def __init__(self, config):
        super().__init__()
        self.setTitle("Components")
        self.setSubTitle("Select which components to install.")
        self._checkboxes = []
        layout = QVBoxLayout(self)
        for comp in config.get("components", []):
            cb = QCheckBox(comp.get("label", comp["id"]))
            cb.setChecked(comp.get("default", True))
            cb.setToolTip(comp.get("description", ""))
            self._checkboxes.append((comp["id"], cb))
            layout.addWidget(cb)
        layout.addStretch()

    def selectedComponents(self):
        return [cid for cid, cb in self._checkboxes if cb.isChecked()]


class ProgressPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Installing")
        self.setSubTitle("Please wait...")
        self._final = False
        layout = QVBoxLayout(self)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        mono = QFont()
        mono.setFamily("monospace")
        mono.setStyleHint(QFont.StyleHint.TypeWriter)
        self.log.setFont(mono)
        layout.addWidget(self.log, 1)

    def isComplete(self):
        return self._final

    def setComplete(self, val: bool):
        self._final = val
        self.completeChanged.emit()


class FinishPage(QWizardPage):
    def __init__(self, config):
        super().__init__()
        self.setFinalPage(True)
        self.setTitle("Installation Complete")
        self._config = config
        self._dest = ""
        layout = QVBoxLayout(self)

        self.label = QLabel(
            "<h3>Installation Complete</h3>"
            "<p>The application has been installed successfully on your system.</p>"
        )
        self.label.setWordWrap(True)
        layout.addWidget(self.label)

        details = QVBoxLayout()
        details.setSpacing(2)

        self._path_label = QLabel()
        self._path_label.setWordWrap(True)
        details.addWidget(self._path_label)

        self._version_label = QLabel()
        self._version_label.setStyleSheet("color: palette(disabled-text); font-size: 9pt;")
        details.addWidget(self._version_label)

        self._size_label = QLabel()
        self._size_label.setStyleSheet("color: palette(disabled-text); font-size: 9pt;")
        details.addWidget(self._size_label)

        self._desktop_label = QLabel()
        self._desktop_label.setStyleSheet("color: palette(disabled-text); font-size: 9pt;")
        details.addWidget(self._desktop_label)

        self._registry_label = QLabel()
        self._registry_label.setStyleSheet("color: green; font-size: 9pt;")
        details.addWidget(self._registry_label)

        layout.addLayout(details)
        layout.addSpacing(8)

        self.launch_cb = QCheckBox("Launch application now")
        self.launch_cb.setChecked(config.get("enable_launch_at_finish", True))
        layout.addWidget(self.launch_cb)

        btn_layout = QHBoxLayout()
        self._open_folder_btn = QPushButton(_theme_icon("folder-open"), "Open Install Folder")
        self._open_folder_btn.clicked.connect(self._on_open_folder)
        self._open_folder_btn.setVisible(False)
        btn_layout.addWidget(self._open_folder_btn)

        self._show_in_niruvi_btn = QPushButton(_theme_icon("go-home"), "Show in Niruvi")
        self._show_in_niruvi_btn.clicked.connect(self._on_show_in_niruvi)
        self._show_in_niruvi_btn.setVisible(False)
        btn_layout.addWidget(self._show_in_niruvi_btn)

        layout.addLayout(btn_layout)
        layout.addStretch(1)

    def setDest(self, path: str):
        self._dest = path
        if not path:
            return
        app_name = self._config.get("app_name", "")
        version = self._config.get("app_version", "")
        self._path_label.setText(f"<b>Installed to:</b> {path}")
        self._version_label.setText(f"<b>Version:</b> {version or '?'}")
        try:
            size = sum(
                os.path.getsize(os.path.join(dp, f))
                for dp, _, fn in os.walk(path)
                for f in fn
            ) if os.path.isdir(path) else 0
            if size > 1073741824:
                size_str = f"{size / 1073741824:.1f} GB"
            elif size > 1048576:
                size_str = f"{size / 1048576:.0f} MB"
            elif size > 1024:
                size_str = f"{size / 1024:.0f} KB"
            else:
                size_str = f"{size} B"
            self._size_label.setText(f"<b>Size:</b> {size_str}")
        except Exception:
            self._size_label.setText("<b>Size:</b> ?")
        desktop_path = os.path.expanduser(f"~/.local/share/applications/{app_name}.desktop")
        if os.path.isfile(desktop_path):
            self._desktop_label.setText(f"<b>Desktop Entry:</b> {desktop_path}")
        else:
            self._desktop_label.setText("<b>Desktop Entry:</b> Created")
        self._registry_label.setText("✓ Registered in Niruvi Application Manager")
        self._open_folder_btn.setVisible(True)
        self._show_in_niruvi_btn.setVisible(True)

    def _on_open_folder(self):
        if self._dest:
            from PyQt6.QtCore import QUrl
            from PyQt6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._dest))

    def _on_show_in_niruvi(self):
        try:
            subprocess.Popen(
                [sys.executable, "-m", "niruvi.main", "--open", self._dest],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    def shouldLaunch(self):
        return self.launch_cb.isChecked()


# ── Main Wizard ──


class SelfInstallWizard(QWizard):
    def __init__(self, config, self_appimage, mode="install"):
        super().__init__()
        self.config = config
        self.self_appimage = self_appimage
        self.mode = mode
        self._worker = None
        self._finish_appimage = None

        self.setWindowTitle(f"Install {config['app_name']}")
        self.setMinimumSize(560, 480)
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)

        if mode == "install":
            self._build_install_pages()
        elif mode == "uninstall":
            self._build_uninstall_pages()
        elif mode == "update":
            self._build_update_pages()

        self.button(QWizard.WizardButton.FinishButton).setEnabled(False)
        self.button(QWizard.WizardButton.BackButton).setEnabled(False)

    def _build_install_pages(self):
        self.addPage(WelcomePage(self.config))
        if LICENSE_PATH.exists():
            self.addPage(LicensePage(self.config))
        self.addPage(DirectoryPage(self.config))
        if self.config.get("components"):
            self.addPage(ComponentsPage(self.config))
        self._progress_page = ProgressPage()
        self.addPage(self._progress_page)
        self._finish_page = FinishPage(self.config)
        self.addPage(self._finish_page)
        self.currentIdChanged.connect(self._on_install_page_changed)

    def _build_uninstall_pages(self):
        p1 = QWizardPage()
        p1.setTitle("Confirm Uninstall")
        p1.setSubTitle("Are you sure you want to remove this application?")
        layout = QVBoxLayout(p1)
        label = QLabel(
            f"<b>{self.config['app_name']}</b> will be removed from:<br>"
            f"<code>{installed_dir(self.config)}</code><br><br>"
            "Desktop entries and icons will also be deleted."
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addStretch()
        self.addPage(p1)

        self._progress_page = ProgressPage()
        self._progress_page.setTitle("Uninstalling")
        self._progress_page.setSubTitle("Removing application files...")
        self.addPage(self._progress_page)

        p_done = QWizardPage()
        p_done.setFinalPage(True)
        p_done.setTitle("Uninstall Complete")
        dl = QVBoxLayout(p_done)
        dl.addStretch(1)
        dl.addWidget(QLabel("<h2>Uninstall Complete</h2><p>The application has been removed.</p>"))
        dl.addStretch(1)
        self.addPage(p_done)

        self.currentIdChanged.connect(self._on_uninstall_page_changed)

    def _build_update_pages(self):
        self._check_page = QWizardPage()
        self._check_page.setTitle("Checking for Updates")
        self._check_page.setSubTitle("Please wait...")
        cl = QVBoxLayout(self._check_page)
        self._check_label = QLabel("Checking for updates...")
        self._check_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.addWidget(self._check_label)
        self.addPage(self._check_page)

        self._avail_page = QWizardPage()
        self._avail_page.setTitle("Update Available")
        self._avail_page.setSubTitle("A new version is available.")
        al = QVBoxLayout(self._avail_page)
        al.addWidget(QLabel("<b>Changelog:</b>"))
        self._changelog = QTextBrowser()
        al.addWidget(self._changelog, 1)
        self.addPage(self._avail_page)

        self._progress_page = ProgressPage()
        self._progress_page.setTitle("Updating")
        self._progress_page.setSubTitle("Downloading and installing update...")
        self.addPage(self._progress_page)

        p_done = QWizardPage()
        p_done.setFinalPage(True)
        p_done.setTitle("Update Complete")
        dl = QVBoxLayout(p_done)
        dl.addStretch(1)
        dl.addWidget(QLabel("<h2>Update Complete</h2><p>The application has been updated.</p>"))
        dl.addStretch(1)
        self.addPage(p_done)

        self.currentIdChanged.connect(self._on_update_page_changed)
        self._manifest = None

    def startInstall(self):
        self._progress_page.progress.setValue(0)
        self._progress_page.log.clear()
        dir_page = self.findChild(DirectoryPage)
        dest = dir_page.installDir() if dir_page else installed_dir(self.config)
        self._worker = InstallWorker(self.config, dest, self.self_appimage)
        self._worker.progress.connect(self._progress_page.progress.setValue)
        self._worker.log.connect(self._progress_page.log.append)
        self._worker.finished.connect(self._on_install_finished)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()
        self.button(QWizard.WizardButton.BackButton).setEnabled(False)

    def _on_install_finished(self, dest):
        self._progress_page.setComplete(True)
        self.button(QWizard.WizardButton.FinishButton).setEnabled(True)
        self._finish_appimage = os.path.join(dest, "AppRun")
        self._finish_page.setDest(dest)

    def _on_worker_error(self, msg):
        try:
            from niruvi.utils.sound_manager import play as play_sound
            play_sound("error")
        except ImportError:
            pass
        QMessageBox.critical(self, "Error", msg)
        self.reject()

    def _on_install_page_changed(self, idx):
        if isinstance(self.page(idx), ProgressPage):
            self.startInstall()

    def accept(self):
        if self._finish_appimage and os.path.isfile(self._finish_appimage):
            if self._finish_page and self._finish_page.shouldLaunch():
                try:
                    proc = subprocess.Popen(
                        [self._finish_appimage],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    import time
                    time.sleep(1)
                    if proc.poll() is not None and proc.returncode != 0:
                        QMessageBox.warning(
                            self, "Launch Issue",
                            "The application was installed but could not be launched. "
                            "You can open the install folder and run it manually.",
                        )
                except Exception:
                    pass
        super().accept()

    def _on_uninstall_page_changed(self, idx):
        if isinstance(self.page(idx), ProgressPage):
            self._progress_page.log.clear()
            self._worker = UninstallWorker(self.config)
            self._worker.progress.connect(self._progress_page.progress.setValue)
            self._worker.log.connect(self._progress_page.log.append)
            self._worker.finished.connect(self._on_uninstall_finished)
            self._worker.error.connect(self._on_worker_error)
            self._worker.start()

    def _on_uninstall_finished(self):
        self._progress_page.setComplete(True)
        self.button(QWizard.WizardButton.FinishButton).setEnabled(True)

    def _on_update_page_changed(self, idx):
        page = self.page(idx)
        if page is self._avail_page and self._manifest:
            self._changelog.setText(self._manifest.get("changelog", "No changelog available."))
        if isinstance(page, ProgressPage):
            if self._manifest:
                self._start_update_download()

    def _start_update_check(self):
        self._worker = UpdateWorker(self.config)
        self._worker.update_available.connect(self._on_update_available)
        self._worker.no_update.connect(self._on_no_update)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

    def _on_update_available(self, manifest):
        self._manifest = manifest
        self._check_label.setText("Update found!")
        self.button(QWizard.WizardButton.NextButton).setEnabled(True)

    def _on_no_update(self):
        self._check_label.setText("Already up to date.")
        self._progress_page.setComplete(True)
        self.button(QWizard.WizardButton.FinishButton).setEnabled(True)

    def _start_update_download(self):
        self._progress_page.log.clear()
        self._worker = UpdateWorker(self.config)
        self._worker.progress.connect(self._progress_page.progress.setValue)
        self._worker.log.connect(self._progress_page.log.append)
        self._worker.finished.connect(self._on_update_finished)
        self._worker.error.connect(self._on_worker_error)
        self._worker.download_and_install(self._manifest, self.self_appimage)

    def _on_update_finished(self, dest):
        self._progress_page.setComplete(True)
        self.button(QWizard.WizardButton.FinishButton).setEnabled(True)


# ── Entry point ──


def main():
    if len(sys.argv) < 2:
        print("Usage: self_install_wizard.py --install|--uninstall|--update|--check-updates",
              file=sys.stderr)
        sys.exit(1)

    raw = sys.argv[1].lstrip("-").replace("-", "_")
    modes = {"install", "uninstall", "update", "check_updates"}
    if raw not in modes:
        print(f"Unknown mode: {raw}", file=sys.stderr)
        sys.exit(1)
    mode = raw

    # config is always next to the script
    config = load_config()

    self_appimage = os.environ.get("APPIMAGE", "")
    if not self_appimage or not os.path.isfile(self_appimage):
        try:
            self_appimage = os.readlink("/proc/self/exe")
        except Exception:
            self_appimage = sys.argv[0]
    # Validate AppImage path
    self_appimage = os.path.realpath(self_appimage)
    if not os.path.isfile(self_appimage):
        print("Error: could not determine the AppImage path.", file=sys.stderr)
        sys.exit(1)
    if ".." in self_appimage.split(os.sep):
        print("Error: AppImage path contains invalid components.", file=sys.stderr)
        sys.exit(1)

    if mode == "check_updates":
        from PyQt6.QtCore import QCoreApplication
        QCoreApplication(sys.argv)
        w = UpdateWorker(config)
        w.no_update.connect(lambda: print("No update available"))
        w.update_available.connect(lambda m: print(f"Update available: {m.get('version')}"))
        w.error.connect(lambda e: print(f"Error: {e}", file=sys.stderr))
        w.run()
        return

    _fix_qt_platform_path()
    app = QApplication(sys.argv)
    wizard = SelfInstallWizard(config, self_appimage, mode)
    if mode == "update":
        wizard._start_update_check()
    wizard.show()
    exit_code = app.exec()

    if mode == "install" and wizard.result() == QWizard.DialogCode.Accepted:
        launch = wizard._finish_appimage or os.path.join(installed_dir(config), "AppRun")
        if launch and os.path.isfile(launch):
            try:
                subprocess.Popen([launch], start_new_session=True)
            except Exception:
                pass

    sys.exit(exit_code if exit_code else 0)

if __name__ == "__main__":
    main()
