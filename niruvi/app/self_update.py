import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, QProcess, Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox, QProgressDialog, QWidget

from niruvi import __version__
from niruvi.utils.http import _create_ssl_context
from niruvi.utils.sound_manager import play as play_sound

logger = logging.getLogger(__name__)

#: Keep background download workers referenced so they are not garbage collected
#  while a download is in flight (mirrors niruvi.core.worker._live_workers).
_active_downloads: list["UpdateDownloadWorker"] = []

UPDATE_MANIFEST_URL = "https://raw.githubusercontent.com/putinservai-cyber/niruvi/main/update.json"
UPDATE_SIGNATURE_URL = UPDATE_MANIFEST_URL + ".asc"

SIGNING_KEY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "signing-key.asc",
)

NIRUVI_APPIMAGE_NAME = "Niruvi-x86_64.AppImage"


def _get_install_dir() -> str:
    """Return the actual Niruvi installation directory.

    Priority:
    1. APPIMAGE env var → directory containing the running AppImage
    2. INSTALLED_DIR from settings (~/Applications/Niruvi)
    3. Fallback to ~/Applications/Niruvi
    """
    appimage = os.environ.get("APPIMAGE")
    if appimage and os.path.isfile(appimage):
        return os.path.dirname(os.path.realpath(appimage))
    from niruvi.config import INSTALLED_DIR

    return os.path.expanduser(INSTALLED_DIR)


def _get_appimage_path() -> str:
    """Return the path to the Niruvi AppImage file."""
    install_dir = _get_install_dir()
    candidate = os.path.join(install_dir, NIRUVI_APPIMAGE_NAME)
    if os.path.isfile(candidate):
        return candidate
    # fallback: look for any .AppImage in the install dir
    for f in os.listdir(install_dir):
        if f.endswith(".AppImage") and f.startswith("Niruvi"):
            return os.path.join(install_dir, f)
    return candidate


def compare_versions(v1, op, v2):
    def parse_version(v):
        v = v.lstrip("vV")
        parts = []
        for p in v.split("."):
            digit = ""
            for ch in p:
                if ch.isdigit():
                    digit += ch
                else:
                    break
            try:
                parts.append(int(digit)) if digit else parts.append(0)
            except ValueError:
                parts.append(0)
        return parts

    v1_parts = parse_version(v1)
    v2_parts = parse_version(v2)

    max_len = max(len(v1_parts), len(v2_parts))
    v1_parts.extend([0] * (max_len - len(v1_parts)))
    v2_parts.extend([0] * (max_len - len(v2_parts)))

    cmp = (v1_parts > v2_parts) - (v1_parts < v2_parts)

    if op == "gt":
        return cmp > 0
    elif op == "eq":
        return cmp == 0
    elif op == "lt":
        return cmp < 0
    return False


def _fetch_json(url: str, timeout: int) -> dict:
    from niruvi.utils.http import fetch_json

    return fetch_json(url, timeout)


class UpdateDownloadWorker(QThread):
    """Streams the update AppImage to disk on a worker thread.

    Emits ``progress`` (0-100), ``finished`` (sha256 digest), ``cancelled``,
    and ``error`` so the UI stays responsive during the multi-hundred-MB download.
    """

    progress = pyqtSignal(int)
    finished = pyqtSignal(bytes)
    cancelled = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, url: str, dest: str, timeout: int = 120):
        super().__init__()
        self._url = url
        self._dest = dest
        self._timeout = timeout
        self._aborted = False

    def abort(self):
        self._aborted = True

    def run(self):
        ctx = _create_ssl_context()
        try:
            resp = urllib.request.urlopen(self._url, timeout=self._timeout, context=ctx)
        except Exception as e:
            self.error.emit(f"Could not start download: {e}")
            return

        total = int(resp.headers.get("Content-Length", 0))
        chunk_size = 8192
        sha256_hash = hashlib.sha256()
        fd = os.open(self._dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as f:
                downloaded = 0
                while True:
                    if self._aborted:
                        os.unlink(self._dest)
                        self.cancelled.emit()
                        return
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    sha256_hash.update(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        self.progress.emit(int((downloaded / total) * 100))
        except Exception as e:
            if os.path.exists(self._dest):
                os.unlink(self._dest)
            self.error.emit(f"Download failed: {e}")
            return

        self.finished.emit(sha256_hash.digest())


def _verify_update_manifest(manifest_json: str) -> bool:
    """Verify the GPG signature on the update manifest.

    Fetches the detached signature from UPDATE_SIGNATURE_URL and verifies
    it against the bundled public key (data/signing-key.asc).
    Returns True if the signature is valid, False otherwise.
    """
    if not os.path.isfile(SIGNING_KEY_PATH):
        logger.error("No signing key found at %s — manifest verification FAILED", SIGNING_KEY_PATH)
        return False

    if not shutil.which("gpg"):
        logger.error("GPG not available — manifest verification FAILED")
        return False

    try:
        ctx = _create_ssl_context()
        req = urllib.request.Request(UPDATE_SIGNATURE_URL, headers={"Accept": "text/plain"})
        resp = urllib.request.urlopen(req, timeout=15, context=ctx)
        sig_data = resp.read().decode("utf-8")
    except Exception as e:
        logger.warning("Failed to fetch signature from %s: %s", UPDATE_SIGNATURE_URL, e)
        return False

    with tempfile.TemporaryDirectory(prefix="niruvi-verify-") as tmpdir:
        manifest_path = os.path.join(tmpdir, "update.json")
        sig_path = os.path.join(tmpdir, "update.json.asc")
        keyring_path = os.path.join(tmpdir, "keyring.gpg")

        with open(manifest_path, "w") as f:
            f.write(manifest_json)
        with open(sig_path, "w") as f:
            f.write(sig_data)

        try:
            subprocess.run(
                ["gpg", "--import", "--no-default-keyring", "--keyring", keyring_path, SIGNING_KEY_PATH],
                capture_output=True,
                timeout=15,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            logger.warning("Failed to import signing key: %s", e.stderr.decode())
            return False

        try:
            result = subprocess.run(
                ["gpg", "--no-default-keyring", "--keyring", keyring_path, "--verify", sig_path, manifest_path],
                capture_output=True,
                timeout=15,
            )
            if result.returncode == 0:
                logger.info("Update manifest GPG signature verified successfully")
                return True
            logger.warning(
                "Update manifest GPG signature INVALID: %s",
                result.stderr.decode().strip(),
            )
            return False
        except subprocess.TimeoutExpired:
            logger.warning("GPG verification timed out")
            return False


def check_for_updates(parent: QWidget):
    current_version = __version__

    try:
        ctx = _create_ssl_context()
        raw_json_resp = urllib.request.urlopen(UPDATE_MANIFEST_URL, timeout=15, context=ctx)
        raw_json = raw_json_resp.read().decode("utf-8")
        manifest = json.loads(raw_json)

        if not _verify_update_manifest(raw_json):
            play_sound("warning")
            QMessageBox.warning(
                parent,
                "Update Check Failed",
                "The update manifest could not be verified. The signature is "
                "invalid or missing.\n\n"
                "This may indicate a tampered update server. "
                "Please try again later or report this issue.",
            )
            return

        latest_version = manifest.get("version", "").lstrip("v")
        download_url = manifest.get("download_url", "")
        expected_sha256 = manifest.get("sha256", "")
        changelog = manifest.get("changelog", "")

        if not latest_version or not download_url:
            play_sound("warning")
            QMessageBox.warning(
                parent, "Update Check", "Update manifest is missing required fields (version, download_url)."
            )
            return

        if compare_versions(latest_version, "gt", current_version):
            msg = (
                f"A new version of Niruvi is available!\n\n"
                f"Current version: {current_version}\n"
                f"New version: {latest_version}\n"
            )
            if changelog:
                msg += f"\nWhat's new:\n{changelog[:500]}"

            play_sound("notification")
            reply = QMessageBox.question(
                parent,
                "Update Available",
                msg + "\n\nDo you want to download and install it now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )

            if reply == QMessageBox.StandardButton.Yes:
                _download_and_install(parent, download_url, expected_sha256, latest_version)
        else:
            play_sound("info")
            QMessageBox.information(
                parent,
                "Up to Date",
                f"Niruvi (version {current_version}) is already the latest version.",
            )

    except Exception as e:
        play_sound("warning")
        QMessageBox.warning(
            parent,
            "Update Check Failed",
            f"Could not check for updates:\n{e}",
        )


def _download_and_install(parent: QWidget, download_url: str, expected_sha256: str, version: str):
    """Download and install an update without blocking the UI thread.

    The download runs on :class:`UpdateDownloadWorker`; all GUI interaction is
    routed through :class:`_InstallCoordinator`, which has main-thread affinity
    so dialogs are shown safely.
    """
    progress = QProgressDialog("Downloading update...", "Cancel", 0, 100, parent)
    progress.setWindowTitle("Downloading Update")
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setAutoClose(True)
    progress.setValue(0)

    fd, temp_path = tempfile.mkstemp(suffix=".AppImage")
    os.close(fd)
    os.chmod(temp_path, 0o600)

    coordinator = _InstallCoordinator(parent, temp_path, version, expected_sha256, progress)
    worker = UpdateDownloadWorker(download_url, temp_path)
    progress.canceled.connect(worker.abort)
    worker.progress.connect(coordinator.on_progress)
    worker.finished.connect(coordinator.on_finished)
    worker.cancelled.connect(coordinator.on_cancelled)
    worker.error.connect(coordinator.on_error)
    worker.finished.connect(worker.deleteLater)
    worker.cancelled.connect(worker.deleteLater)
    worker.error.connect(worker.deleteLater)
    _active_downloads.append(worker)
    worker.finished.connect(lambda: _active_downloads.remove(worker) if worker in _active_downloads else None)
    worker.start()


class _InstallCoordinator(QObject):
    """Runs on the GUI thread; owns the download result and all dialogs."""

    def __init__(self, parent: QWidget, temp_path: str, version: str, expected_sha256: str, progress: QProgressDialog):
        super().__init__()
        self._parent = parent
        self._temp_path: str | None = temp_path
        self._version = version
        self._expected = expected_sha256
        self._progress = progress

    def on_progress(self, val: int):
        if not self._progress.wasCanceled():
            self._progress.setValue(val)

    def on_cancelled(self):
        self._progress.close()
        if self._temp_path:
            Path(self._temp_path).unlink(missing_ok=True)
        self.deleteLater()

    def on_error(self, msg: str):
        self._progress.close()
        if self._temp_path:
            Path(self._temp_path).unlink(missing_ok=True)
        play_sound("error")
        QMessageBox.critical(self._parent, "Update Failed", f"Failed to download or install update:\n{msg}")
        self.deleteLater()

    def on_finished(self, digest: bytes):
        self._progress.close()
        if self._expected:
            actual = digest.hex()
            if actual.lower() != self._expected.lower():
                if self._temp_path:
                    Path(self._temp_path).unlink(missing_ok=True)
                play_sound("error")
                QMessageBox.critical(
                    self._parent,
                    "Verification Failed",
                    f"SHA256 mismatch!\n\n"
                    f"Expected: {self._expected}\n"
                    f"Actual:   {actual}\n\n"
                    "The downloaded file may be corrupted or tampered with.",
                )
                self.deleteLater()
                return
        self._install()

    def _install(self):
        backup_path: str | None = None
        dest: str | None = None
        try:
            install_dir = _get_install_dir()
            os.makedirs(install_dir, exist_ok=True)
            dest = os.path.join(install_dir, NIRUVI_APPIMAGE_NAME)

            backup_path = dest + ".backup"
            if os.path.exists(dest):
                if os.path.exists(backup_path):
                    os.remove(backup_path)
                os.rename(dest, backup_path)

            if self._temp_path:
                os.replace(self._temp_path, dest)
            os.chmod(dest, 0o755)
            self._temp_path = None

            meta_path = os.path.join(install_dir, ".appimage-manager.json")
            meta: dict[str, Any] = {}
            if os.path.exists(meta_path):
                with open(meta_path) as f:
                    meta = json.load(f)
            meta["version"] = self._version
            meta["last_update"] = str(int(time.time()))
            with open(meta_path, "w") as f:
                json.dump(meta, f)

            if backup_path and os.path.exists(backup_path):
                os.remove(backup_path)
            backup_path = None

            play_sound("success")
            reply = QMessageBox.question(
                self._parent,
                "Update Installed",
                f"Niruvi has been updated to version {self._version}.\n\n"
                f"Location: {dest}\n\n"
                "Do you want to launch the new version now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )

            if reply == QMessageBox.StandardButton.Yes:
                QProcess.startDetached(str(dest), [])
                # Force a full teardown so the old instance does not linger in the
                # tray and contend for the SQLite registry. Setting _really_quit
                # bypasses the tray-aware closeEvent that parent.close() would
                # otherwise honour (it would hide to tray and keep running).
                app = QApplication.instance()
                if isinstance(app, QApplication):
                    for w in app.topLevelWidgets():
                        if hasattr(w, "_really_quit"):
                            w._really_quit = True
                    app.quit()
        except Exception as e:
            orig_error = e
            if backup_path and os.path.exists(backup_path) and dest:
                try:
                    if os.path.exists(dest):
                        os.remove(dest)
                    os.rename(backup_path, dest)
                except Exception as rb_e:
                    logger.debug("Failed to restore backup during update rollback: %s", rb_e, exc_info=True)
            if self._temp_path:
                Path(self._temp_path).unlink(missing_ok=True)
            play_sound("error")
            QMessageBox.critical(self._parent, "Update Failed", f"Failed to download or install update:\n{orig_error}")
        finally:
            self.deleteLater()
