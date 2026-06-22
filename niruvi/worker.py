import os
import shutil
import subprocess
import tempfile

from PyQt6.QtCore import QThread, pyqtSignal


def _find_extracted_dir(extract_dir: str) -> str:
    extracted_dir = os.path.join(extract_dir, "squashfs-root")
    if os.path.isdir(extracted_dir):
        return extracted_dir
    dirs = [d for d in os.listdir(extract_dir) if os.path.isdir(os.path.join(extract_dir, d))]
    if dirs:
        return os.path.join(extract_dir, dirs[0])
    raise RuntimeError("No extracted directory found.")


def _run_extraction(appimage_path: str, extract_dir: str):
    os.chmod(appimage_path, 0o755)
    proc = subprocess.Popen(
        [appimage_path, "--appimage-extract"],
        cwd=extract_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = proc.communicate(timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"Extraction failed: {stderr.strip()}")
    return _find_extracted_dir(extract_dir)


def _atomic_install(extracted_dir: str, dest_dir: str):
    """Copy extracted_dir to a staging location on the same filesystem as dest_dir,
    then atomically rename staging → dest_dir."""
    parent = os.path.dirname(dest_dir)
    os.makedirs(parent, exist_ok=True)
    staging = dest_dir + ".staging"
    if os.path.exists(staging):
        shutil.rmtree(staging)
    shutil.copytree(extracted_dir, staging, dirs_exist_ok=True)
    os.replace(staging, dest_dir)


def extract_appimage_sync(appimage_path: str, dest_dir: str) -> None:
    """Extract an AppImage synchronously (no Qt threading), atomic install."""
    with tempfile.TemporaryDirectory() as extract_dir:
        extracted_dir = _run_extraction(appimage_path, extract_dir)
        _atomic_install(extracted_dir, dest_dir)


class ExtractionWorker(QThread):
    extraction_finished = pyqtSignal(str, str)
    extraction_error = pyqtSignal(str)
    progress_updated = pyqtSignal(int)
    log_message = pyqtSignal(str)

    def __init__(self, appimage_path: str, dest_dir: str, app_name: str, parent=None):
        super().__init__(parent)
        self.appimage_path = appimage_path
        self.dest_dir = dest_dir
        self.app_name = app_name
        self._process = None

    def run(self):
        try:
            self.log_message.emit(f"Extracting {self.appimage_path}...")
            self.progress_updated.emit(10)

            with tempfile.TemporaryDirectory() as extract_dir:
                self.log_message.emit("Running --appimage-extract...")
                self.progress_updated.emit(20)

                proc = subprocess.Popen(
                    [self.appimage_path, "--appimage-extract"],
                    cwd=extract_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                self._process = proc
                stdout, stderr = proc.communicate(timeout=300)
                if proc.returncode != 0:
                    self.extraction_error.emit(f"Extraction failed: {stderr}")
                    return

                self.progress_updated.emit(50)
                self.log_message.emit("Extraction complete. Copying files...")

                try:
                    extracted_dir = _find_extracted_dir(extract_dir)
                except RuntimeError as e:
                    self.extraction_error.emit(str(e))
                    return

                self.progress_updated.emit(70)
                _atomic_install(extracted_dir, self.dest_dir)
                self.progress_updated.emit(90)

                if not os.path.isdir(self.dest_dir):
                    self.extraction_error.emit(f"Destination '{self.dest_dir}' not created.")
                    return

                self.progress_updated.emit(100)
                self.log_message.emit("Extraction complete.")
                self.extraction_finished.emit(self.dest_dir, self.app_name)

        except subprocess.TimeoutExpired:
            if self._process:
                self._process.kill()
            self.extraction_error.emit("Extraction timed out.")
        except subprocess.CalledProcessError as e:
            self.extraction_error.emit(f"Extraction failed: {e.stderr}")
        except Exception as e:
            self.extraction_error.emit(str(e))

    def stop(self):
        if self._process and self._process.poll() is None:
            try:
                self._process.terminate()
                self._process.wait(5)
            except Exception:
                pass
