import atexit
import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request

from PyQt6.QtCore import QThread, pyqtSignal

from niruvi.core.scanner import extract_safely

logger = logging.getLogger(__name__)

_live_workers: list[QThread] = []


def start_worker(worker: QThread) -> None:
    """Start a QThread worker and keep it referenced until it finishes.

    Prevents the Qt fatal "QThread: Destroyed while thread is still running"
    (SIGABRT) that happens when a running worker's last Python reference is
    dropped — e.g. a slot reassigns the worker attribute or a dialog is
    closed mid-operation.
    """
    if worker not in _live_workers:
        _live_workers.append(worker)
        worker.finished.connect(lambda w=worker: _finish_worker(w))
    worker.start()


def _finish_worker(worker: QThread) -> None:
    if worker in _live_workers:
        _live_workers.remove(worker)
    worker.deleteLater()


def wait_for_workers(timeout_ms: int = 10000) -> None:
    """Wait for still-running workers. Call after the event loop has exited."""
    deadline = time.monotonic() * 1000 + timeout_ms
    for worker in list(_live_workers):
        if not worker.isRunning():
            continue
        remaining = deadline - time.monotonic() * 1000
        if remaining <= 0:
            break
        worker.wait(int(remaining))


_REMOVABLE_PREFIXES = ("mtp:", "gvfs", "/media/", "/run/media/", "/mnt/")


def _cleanup_temp_copies():
    tmp_dir = os.path.join(tempfile.gettempdir(), "niruvi_local_copy")
    if os.path.isdir(tmp_dir):
        shutil.rmtree(tmp_dir, ignore_errors=True)


atexit.register(_cleanup_temp_copies)


def _is_removable_path(path: str) -> bool:
    resolved = os.path.realpath(os.path.expanduser(path))
    for p in _REMOVABLE_PREFIXES:
        if p.startswith("/") and resolved.startswith(p):
            return True
        if f"/{p}" in resolved or resolved.startswith(p):
            return True
    return False


def _ensure_local(path: str, log) -> str:
    """If path is on a removable device, copy it to a local temp location.

    Returns the (possibly new) local path.
    """
    if not _is_removable_path(path):
        return path
    log("Source is on removable media — copying to local temp first...")
    local = os.path.join(tempfile.gettempdir(), "niruvi_local_copy", os.path.basename(path))
    os.makedirs(os.path.dirname(local), exist_ok=True)
    shutil.copy2(path, local)
    os.chmod(local, 0o755)
    log(f"Local copy: {local}")
    return local


def _find_extracted_dir(extract_dir: str) -> str:
    extracted_dir = os.path.join(extract_dir, "squashfs-root")
    if os.path.isdir(extracted_dir):
        return extracted_dir
    dirs = [d for d in os.listdir(extract_dir) if os.path.isdir(os.path.join(extract_dir, d))]
    if dirs:
        return os.path.join(extract_dir, dirs[0])
    raise RuntimeError("No extracted directory found.")


def _check_disk_space(path: str, extract_dir: str) -> None:
    """Check that extract_dir has enough free space for the AppImage."""
    appimage_size = os.path.getsize(path)
    free = shutil.disk_usage(extract_dir).free
    if free < appimage_size * 2:
        needed = _format_bytes(appimage_size * 2)
        available = _format_bytes(free)
        raise OSError(
            f"Not enough disk space. Need ~{needed}, only {available} available.\n"
            f"AppImage size: {_format_bytes(appimage_size)}"
        )


def _format_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _run_extraction(appimage_path: str, extract_dir: str, log=None, process_tracker: list | None = None):
    path = _ensure_local(appimage_path, log or (lambda m: None))
    _check_disk_space(path, extract_dir)
    safe_dir = os.path.join(extract_dir, "squashfs-root")
    if extract_safely(path, safe_dir):
        extracted = _find_extracted_dir(extract_dir)
        if extracted:
            return extracted
    os.chmod(path, 0o755)
    proc = subprocess.Popen(
        [path, "--appimage-extract"],
        cwd=extract_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if process_tracker is not None:
        process_tracker.append(proc)
    _stdout, stderr = proc.communicate(timeout=300)
    if proc.returncode != 0:
        msg = stderr.strip()
        if "Quota exceeded" in msg or "quota" in msg.lower() or "No space left" in msg:
            msg = "Not enough disk space to extract the AppImage. Free up space and try again."
        raise RuntimeError(f"Extraction failed: {msg}")
    return _find_extracted_dir(extract_dir)


def _atomic_install(extracted_dir: str, dest_dir: str):
    """Copy extracted_dir to dest_dir, removing dest_dir first if it exists.
    Uses a staging directory for crash safety — if the copy fails, dest_dir is untouched."""
    parent = os.path.dirname(dest_dir)
    os.makedirs(parent, exist_ok=True)
    staging = dest_dir + ".staging"
    if os.path.exists(staging):
        shutil.rmtree(staging, ignore_errors=True)
    try:
        shutil.copytree(extracted_dir, staging, dirs_exist_ok=True, symlinks=True, ignore_dangling_symlinks=True)
        if os.path.exists(dest_dir):
            shutil.rmtree(dest_dir)
        os.replace(staging, dest_dir)
    except Exception as e:
        logger.debug("Atomic install copy failed, rolling back staging dir: %s", e, exc_info=True)
        if os.path.exists(staging):
            shutil.rmtree(staging, ignore_errors=True)
        raise


def extract_appimage_sync(appimage_path: str, dest_dir: str) -> None:
    """Extract an AppImage synchronously (no Qt threading), atomic install."""
    with tempfile.TemporaryDirectory() as extract_dir:
        extracted_dir = _run_extraction(appimage_path, extract_dir)
        _check_junest_destination(extracted_dir, dest_dir)
        _atomic_install(extracted_dir, dest_dir)


def _check_junest_destination(extracted_dir: str, dest_dir: str) -> None:
    """Refuse to install a JuNest container app into a path with spaces.

    JuNest's bundled bubblewrap/proot scripts word-split unquoted shell
    variables, so any space in the path breaks the app at launch.  Raising
    here happens *before* anything is copied to the destination.
    """
    from niruvi.installer.junest import JunestPathError, junest_destination_error

    error = junest_destination_error(extracted_dir, dest_dir)
    if error:
        from niruvi.installer.junest import suggest_space_free_path

        logger.error("Refusing JuNest install into path with spaces: %s", dest_dir)
        raise JunestPathError(
            error,
            suggested_path=suggest_space_free_path(dest_dir),
        )


def _check_junest_destination_soft(extracted_dir: str, dest_dir: str) -> str | None:
    """Non-raising variant for the GUI worker: returns the error message or None."""
    from niruvi.installer.junest import junest_destination_error

    return junest_destination_error(extracted_dir, dest_dir)


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
                self.log_message.emit("Extracting (safe mode)...")
                self.progress_updated.emit(20)

                _proc_tracker: list = []
                extracted_dir_path = _run_extraction(
                    self.appimage_path, extract_dir, self.log_message.emit, _proc_tracker
                )
                if _proc_tracker:
                    self._process = _proc_tracker[0]

                junest_error = _check_junest_destination_soft(extracted_dir_path, self.dest_dir)
                if junest_error:
                    self.log_message.emit(f"ERROR: {junest_error}")
                    self.extraction_error.emit("JUNEST_PATH:" + junest_error)
                    return

                self.progress_updated.emit(50)
                self.log_message.emit("Extraction complete. Copying files...")

                self.progress_updated.emit(70)
                _atomic_install(extracted_dir_path, self.dest_dir)
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
            except Exception as e:
                logger.debug("Failed to terminate extraction process: %s", e, exc_info=True)
        staging = self.dest_dir + ".staging"
        if os.path.exists(staging):
            shutil.rmtree(staging, ignore_errors=True)


class DownloadWorker(QThread):
    """Downloads an AppImage from a URL with progress reporting.

    When `seed_path` points at a previous copy of the file, a zsync
    delta transfer is attempted first (HTTP Range requests for only the
    changed blocks), falling back to a full download on any failure.
    """

    progress_updated = pyqtSignal(int)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    speed_updated = pyqtSignal(str)
    status_changed = pyqtSignal(str)

    def __init__(
        self,
        url: str,
        dest_path: str,
        expected_sha256: str = "",
        parent=None,
        seed_path: str = "",
    ):
        super().__init__(parent)
        self.url = url
        self.dest_path = dest_path
        self.expected_sha256 = expected_sha256
        self.seed_path = seed_path
        self._cancelled = False

    def run(self):
        try:
            if self.seed_path and self.url.startswith(("http://", "https://")):
                self.status_changed.emit("Checking for delta update (.zsync)...")
                from niruvi.desktop.zsync import try_delta_download

                def on_progress(got: int, total: int):
                    if total > 0:
                        self.progress_updated.emit(int((got / total) * 100))

                ok, msg = try_delta_download(self.url, self.seed_path, self.dest_path, progress_cb=on_progress)
                if self._cancelled:
                    self.error.emit("cancelled")
                    return
                if ok:
                    if self.expected_sha256:
                        with open(self.dest_path, "rb") as f:
                            actual_sha = hashlib.sha256(f.read()).hexdigest()
                        if actual_sha.lower() != self.expected_sha256.lower():
                            self.error.emit(f"SHA256 mismatch\nExpected: {self.expected_sha256}\nActual: {actual_sha}")
                            if os.path.exists(self.dest_path):
                                os.unlink(self.dest_path)
                            return
                    self.status_changed.emit("Delta update applied")
                    self.finished.emit(self.dest_path)
                    return
                self.status_changed.emit(f"Delta unavailable ({msg}) - downloading full file")

            from niruvi.utils.http import _create_ssl_context

            resp = urllib.request.urlopen(self.url, timeout=120, context=_create_ssl_context())
            total = int(resp.headers.get("Content-Length", 0))
            chunk_size = 65536
            downloaded = 0
            sha256_hash = hashlib.sha256()
            start_time = 0.0
            import time

            start_time = time.time()

            with open(self.dest_path, "wb") as fh:
                while True:
                    if self._cancelled:
                        self.error.emit("cancelled")
                        return
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    fh.write(chunk)
                    sha256_hash.update(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = int((downloaded / total) * 100)
                        self.progress_updated.emit(pct)
                    elapsed = time.time() - start_time
                    if elapsed > 0:
                        speed = downloaded / elapsed / 1024
                        if speed > 1024:
                            self.speed_updated.emit(f"{speed / 1024:.1f} MB/s")
                        else:
                            self.speed_updated.emit(f"{int(speed)} KB/s")

            actual_sha = sha256_hash.hexdigest()
            if self.expected_sha256 and actual_sha.lower() != self.expected_sha256.lower():
                self.error.emit(f"SHA256 mismatch\nExpected: {self.expected_sha256}\nActual: {actual_sha}")
                if os.path.exists(self.dest_path):
                    os.unlink(self.dest_path)
                return

            self.finished.emit(self.dest_path)
        except Exception as e:
            self.error.emit(str(e))

    def cancel(self):
        self._cancelled = True


class InstallQueueWorker(QThread):
    """Processes a queue of install tasks sequentially."""

    task_started = pyqtSignal(str, int)
    task_progress = pyqtSignal(int)
    task_finished = pyqtSignal(str)
    task_error = pyqtSignal(str)
    task_log = pyqtSignal(str)
    all_finished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue: list[dict] = []
        self._running = False

    def add_task(self, task: dict):
        self._queue.append(task)

    def add_tasks(self, tasks: list[dict]):
        self._queue.extend(tasks)

    def task_count(self) -> int:
        return len(self._queue)

    def run(self):
        self._running = True
        for i, task in enumerate(self._queue):
            if not self._running:
                break
            appimage_path = task.get("appimage_path", "")
            dest_dir = task.get("dest_dir", "")
            app_name = task.get("app_name", "")
            self.task_started.emit(app_name, i + 1)

            worker = ExtractionWorker(appimage_path, dest_dir, app_name, None)
            worker.progress_updated.connect(self.task_progress.emit)
            worker.extraction_finished.connect(self.task_finished.emit)
            worker.extraction_error.connect(self.task_error.emit)
            worker.log_message.connect(self.task_log.emit)
            worker.run()
            worker.wait()

        self.all_finished.emit()
        self._running = False

    def stop(self):
        self._running = False
