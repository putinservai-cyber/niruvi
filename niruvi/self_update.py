import hashlib
import json
import os
import shutil
import tempfile
import time
import urllib.request
from pathlib import Path

from PyQt6.QtCore import Qt, QProcess
from PyQt6.QtWidgets import QMessageBox, QProgressDialog, QWidget

from niruvi import __version__

UPDATE_MANIFEST_URL = "https://raw.githubusercontent.com/putinservai-cyber/niruvi/main/update.json"

NIRUVI_INSTALL_DIR = os.path.expanduser("~/Applications/Niruvi")
NIRUVI_APPIMAGE_NAME = "Niruvi-x86_64.AppImage"


def compare_versions(v1, op, v2):
    def parse_version(v):
        parts = []
        for p in v.split('.'):
            try:
                parts.append(int(p))
            except ValueError:
                parts.append(0)
        return parts

    v1_parts = parse_version(v1)
    v2_parts = parse_version(v2)

    max_len = max(len(v1_parts), len(v2_parts))
    v1_parts.extend([0] * (max_len - len(v1_parts)))
    v2_parts.extend([0] * (max_len - len(v2_parts)))

    cmp = (v1_parts > v2_parts) - (v1_parts < v2_parts)

    if op == 'gt':
        return cmp > 0
    elif op == 'eq':
        return cmp == 0
    elif op == 'lt':
        return cmp < 0
    return False


def _fetch_json(url: str, timeout: int) -> dict:
    resp = urllib.request.urlopen(url, timeout=timeout)
    return json.loads(resp.read().decode("utf-8"))


def _download_file(url: str, dest: str, progress: QProgressDialog) -> bytes:
    resp = urllib.request.urlopen(url, timeout=120)
    total = int(resp.headers.get("Content-Length", 0))
    chunk_size = 8192
    sha256_hash = hashlib.sha256()

    with open(dest, "wb") as f:
        downloaded = 0
        while True:
            if progress.wasCanceled():
                return None
            chunk = resp.read(chunk_size)
            if not chunk:
                break
            f.write(chunk)
            sha256_hash.update(chunk)
            downloaded += len(chunk)
            if total > 0:
                progress.setValue(int((downloaded / total) * 100))

    return sha256_hash.digest()


def check_for_updates(parent: QWidget):
    current_version = __version__

    try:
        manifest = _fetch_json(UPDATE_MANIFEST_URL, 15)

        latest_version = manifest.get("version", "").lstrip("v")
        download_url = manifest.get("download_url", "")
        expected_sha256 = manifest.get("sha256", "")
        changelog = manifest.get("changelog", "")

        if not latest_version or not download_url:
            QMessageBox.warning(
                parent, "Update Check",
                "Update manifest is missing required fields (version, download_url)."
            )
            return

        if compare_versions(latest_version, 'gt', current_version):
            msg = (
                f"A new version of Niruvi is available!\n\n"
                f"Current version: {current_version}\n"
                f"New version: {latest_version}\n"
            )
            if changelog:
                msg += f"\nWhat's new:\n{changelog[:500]}"

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
            QMessageBox.information(
                parent,
                "Up to Date",
                f"Niruvi (version {current_version}) is already the latest version.",
            )

    except Exception as e:
        QMessageBox.warning(
            parent,
            "Update Check Failed",
            f"Could not check for updates:\n{e}",
        )


def _download_and_install(parent: QWidget, download_url: str, expected_sha256: str, version: str):
    progress = QProgressDialog("Downloading update...", "Cancel", 0, 100, parent)
    progress.setWindowTitle("Downloading Update")
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setAutoClose(True)
    progress.setValue(0)

    try:
        fd, temp_path = tempfile.mkstemp(suffix=".AppImage")
        os.close(fd)

        digest = _download_file(download_url, temp_path, progress)
        progress.close()

        if digest is None:
            Path(temp_path).unlink(missing_ok=True)
            return

        if expected_sha256:
            actual = hashlib.sha256(Path(temp_path).read_bytes()).hexdigest()
            if actual.lower() != expected_sha256.lower():
                Path(temp_path).unlink(missing_ok=True)
                QMessageBox.critical(
                    parent, "Verification Failed",
                    f"SHA256 mismatch!\n\n"
                    f"Expected: {expected_sha256}\n"
                    f"Actual:   {actual}\n\n"
                    "The downloaded file may be corrupted or tampered with.",
                )
                return

        os.makedirs(NIRUVI_INSTALL_DIR, exist_ok=True)
        dest = os.path.join(NIRUVI_INSTALL_DIR, NIRUVI_APPIMAGE_NAME)

        backup_path = dest + ".backup"
        if os.path.exists(dest):
            if os.path.exists(backup_path):
                os.remove(backup_path)
            os.rename(dest, backup_path)

        shutil.copy2(temp_path, dest)
        os.chmod(dest, 0o755)
        Path(temp_path).unlink(missing_ok=True)

        meta_path = os.path.join(NIRUVI_INSTALL_DIR, ".appimage-manager.json")
        meta = {}
        if os.path.exists(meta_path):
            with open(meta_path) as f:
                meta = json.load(f)
        meta["version"] = version
        meta["last_update"] = str(int(time.time()))
        with open(meta_path, "w") as f:
            json.dump(meta, f)

        if os.path.exists(backup_path):
            os.remove(backup_path)

        reply = QMessageBox.question(
            parent,
            "Update Installed",
            f"Niruvi has been updated to version {version}.\n\n"
            f"Location: {dest}\n\n"
            "Do you want to launch the new version now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )

        if reply == QMessageBox.StandardButton.Yes:
            QProcess.startDetached(str(dest), [])
            parent.close()

    except Exception as e:
        if os.path.exists(backup_path):
            if os.path.exists(dest):
                os.remove(dest)
            os.rename(backup_path, dest)
        QMessageBox.critical(parent, "Update Failed", f"Failed to download or install update:\n{e}")
