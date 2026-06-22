import hashlib
import json
import os
import tempfile
from pathlib import Path

from PyQt6.QtCore import Qt
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


def check_for_updates(parent: QWidget):
    current_version = __version__

    try:
        import requests
    except ImportError:
        QMessageBox.information(
            parent,
            "Update Check",
            f"Niruvi {current_version}\n\n"
            "To enable automatic update checking, install the 'requests' package:\n"
            "  pip install requests\n\n"
            "Or check manually at the GitHub releases page.",
        )
        return

    try:
        response = requests.get(UPDATE_MANIFEST_URL, timeout=15)
        response.raise_for_status()
        manifest = response.json()

        latest_version = manifest.get("version", "").lstrip("v")
        download_url = manifest.get("download_url", "")
        expected_sha256 = manifest.get("sha256", "")
        changelog = manifest.get("changelog", "")

        if not latest_version or not download_url:
            QMessageBox.warning(parent, "Update Check", "Update manifest is missing required fields (version, download_url).")
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
    try:
        import requests
    except ImportError:
        QMessageBox.critical(parent, "Error", "Requests library not available.")
        return

    try:
        progress = QProgressDialog("Downloading update...", "Cancel", 0, 100, parent)
        progress.setWindowTitle("Downloading Update")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setAutoClose(True)
        progress.setValue(0)

        response = requests.get(download_url, stream=True, timeout=120)
        response.raise_for_status()

        total_length = int(response.headers.get('content-length', 0))
        chunk_size = 8192
        sha256_hash = hashlib.sha256()

        with tempfile.NamedTemporaryFile(suffix=".AppImage", delete=False) as tmp_file:
            downloaded = 0
            for chunk in response.iter_content(chunk_size=chunk_size):
                if progress.wasCanceled():
                    Path(tmp_file.name).unlink(missing_ok=True)
                    return
                tmp_file.write(chunk)
                sha256_hash.update(chunk)
                downloaded += len(chunk)
                if total_length > 0:
                    percent = int((downloaded / total_length) * 100)
                    progress.setValue(percent)
            temp_path = tmp_file.name

        progress.close()

        # SHA256 verification
        if expected_sha256:
            actual_sha256 = sha256_hash.hexdigest()
            if actual_sha256.lower() != expected_sha256.lower():
                Path(temp_path).unlink(missing_ok=True)
                QMessageBox.critical(
                    parent, "Verification Failed",
                    f"SHA256 mismatch!\n\n"
                    f"Expected: {expected_sha256}\n"
                    f"Actual:   {actual_sha256}\n\n"
                    "The downloaded file may be corrupted or tampered with.",
                )
                return

        os.makedirs(NIRUVI_INSTALL_DIR, exist_ok=True)
        dest = os.path.join(NIRUVI_INSTALL_DIR, NIRUVI_APPIMAGE_NAME)

        # Backup current version
        backup_path = dest + ".backup"
        if os.path.exists(dest):
            if os.path.exists(backup_path):
                os.remove(backup_path)
            os.rename(dest, backup_path)

        import shutil
        shutil.copy2(temp_path, dest)
        os.chmod(dest, 0o755)
        Path(temp_path).unlink(missing_ok=True)

        # Update metadata
        meta_path = os.path.join(NIRUVI_INSTALL_DIR, ".appimage-manager.json")
        meta = {}
        if os.path.exists(meta_path):
            with open(meta_path) as f:
                meta = json.load(f)
        meta["version"] = version
        meta["last_update"] = str(int(__import__("time").time()))
        with open(meta_path, "w") as f:
            json.dump(meta, f)

        # Remove backup on success
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
            from PyQt6.QtCore import QProcess
            QProcess.startDetached(str(dest), [])
            parent.close()

    except Exception as e:
        # Restore backup if available
        if os.path.exists(backup_path):
            if os.path.exists(dest):
                os.remove(dest)
            os.rename(backup_path, dest)
        QMessageBox.critical(parent, "Update Failed", f"Failed to download or install update:\n{e}")
