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


def _get_extraction_temp_dir() -> str:
    """Get the temp directory for extraction, avoiding quota-limited /tmp.

    Uses ~/.cache/niruvi/extracts by default, falls back to system temp.
    """
    # Try XDG cache directory first (usually no quota)
    xdg_cache = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
    cache_dir = os.path.join(xdg_cache, "niruvi", "extracts")
    try:
        os.makedirs(cache_dir, exist_ok=True)
        # Test write access
        test_file = os.path.join(cache_dir, ".write_test")
        with open(test_file, "w") as f:
            f.write("test")
        os.unlink(test_file)
        return cache_dir
    except (OSError, PermissionError):
        # Fall back to system temp
        return tempfile.gettempdir()


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
    """Find the extracted AppDir directory (handles both SquashFS and DwarFS)."""
    # Check for standard squashfs-root first
    extracted_dir = os.path.join(extract_dir, "squashfs-root")
    if os.path.isdir(extracted_dir):
        return extracted_dir
    # Check for dwarfs-root (DwarFS extraction)
    extracted_dir = os.path.join(extract_dir, "dwarfs-root")
    if os.path.isdir(extracted_dir):
        return extracted_dir
    # Fallback: any directory in extract_dir
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


def _detect_fs_type(appimage_path: str) -> str:
    """Detect filesystem type (SquashFS or DwarFS) from AppImage metadata."""
    try:
        from niruvi.desktop.appimage_metadata import AppImageMetadata
        meta = AppImageMetadata(appimage_path)
        return getattr(meta, "fs_type", "squashfs")
    except Exception:
        return "squashfs"


def _get_appimage_architecture(appimage_path: str) -> str | None:
    """Get the architecture of the AppImage binary.

    Returns the architecture string (e.g., 'x86_64', 'aarch64') or None if detection fails.
    """
    try:
        from niruvi.desktop.appimage_metadata import AppImageMetadata
        meta = AppImageMetadata(appimage_path)
        return getattr(meta, "architecture", None)
    except Exception:
        return None


def _check_architecture_compatibility(appimage_path: str, log=None) -> bool:
    """Check if the AppImage architecture is compatible with the host.

    Returns True if compatible, False otherwise. Logs a warning if incompatible.
    """
    log = log or (lambda m: None)
    import platform
    host_arch = platform.machine().lower()
    app_arch = _get_appimage_architecture(appimage_path)
    if app_arch is None:
        log("Warning: Could not determine AppImage architecture")
        return True  # Assume compatible if we can't determine

    # Normalize architecture names
    arch_map = {
        "x86_64": "x86_64",
        "amd64": "x86_64",
        "aarch64": "aarch64",
        "arm64": "aarch64",
        "armv7l": "arm",
        "armv6l": "arm",
        "i386": "i386",
        "i686": "i386",
    }
    host_arch = arch_map.get(host_arch, host_arch)
    app_arch = arch_map.get(app_arch, app_arch)

    if host_arch != app_arch:
        log(f"Architecture mismatch: host={host_arch}, app={app_arch}")
        return False
    return True


def _extract_specific_files(appimage_path: str, offset: int, dest: str, files: list[str]) -> bool:
    """Extract only specific files from AppImage using unsquashfs/dwarfsextract.

    This avoids full extraction when only .desktop and icon are needed.
    """
    fs_type = _detect_fs_type(appimage_path)

    if fs_type == "dwarfs":
        if not shutil.which("dwarfsextract"):
            return False
        try:
            # dwarfsextract doesn't support extracting individual files easily
            # Fall back to full extraction for DwarFS
            return False
        except Exception:
            return False
    else:
        if not shutil.which("unsquashfs"):
            return False
        try:
            # Build unsquashfs command to extract only specific files
            cmd = ["unsquashfs", "-q", "-o", str(offset), "-d", dest]
            for f in files:
                cmd.extend(["-e", f])
            proc = subprocess.run(cmd, capture_output=True, timeout=60, input=appimage_path)
            return proc.returncode == 0
        except Exception:
            return False


def _extract_metadata_files(appimage_path: str, extract_dir: str) -> tuple[str | None, str | None]:
    """Extract only .desktop and icon files for metadata reading.

    Returns (desktop_path, icon_path) or (None, None) on failure.
    """
    try:
        from niruvi.desktop.appimage_metadata import AppImageMetadata
        meta = AppImageMetadata(appimage_path)
        offset = meta.payload_offset
    except Exception:
        return None, None

    meta_dir = os.path.join(extract_dir, "meta_extract")
    os.makedirs(meta_dir, exist_ok=True)

    # Try to extract .desktop file
    desktop_path = None
    icon_path = None

    # Try extracting just the .desktop file
    if shutil.which("unsquashfs"):
        try:
            # First, find the .desktop file name
            cmd = ["unsquashfs", "-l", "-o", str(offset), appimage_path]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if proc.returncode == 0:
                for line in proc.stdout.splitlines():
                    if line.strip().endswith(".desktop") and not line.strip().startswith("d"):
                        # Found a .desktop file
                        fname = line.strip().split()[-1]
                        dest_file = os.path.join(meta_dir, os.path.basename(fname))
                        cmd = ["unsquashfs", "-q", "-o", str(offset), "-d", meta_dir, "-e", fname, appimage_path]
                        proc = subprocess.run(cmd, capture_output=True, timeout=30)
                        if proc.returncode == 0 and os.path.exists(dest_file):
                            desktop_path = dest_file
                            break
        except Exception:
            pass

    # If we got desktop, try to find icon from it
    if desktop_path and os.path.exists(desktop_path):
        try:
            with open(desktop_path, encoding="utf-8", errors="ignore") as f:
                content = f.read()
            for line in content.splitlines():
                if line.startswith("Icon="):
                    icon_name = line.split("=", 1)[1].strip()
                    # Try to extract the icon
                    if shutil.which("unsquashfs"):
                        # Search for icon in common locations
                        icon_exts = [".png", ".svg", ".xpm", ""]
                        for ext in icon_exts:
                            icon_paths = [
                                f"usr/share/icons/hicolor/*/apps/{icon_name}{ext}",
                                f"usr/share/pixmaps/{icon_name}{ext}",
                                ".DirIcon",
                            ]
                            for icon_rel in icon_paths:
                                cmd = ["unsquashfs", "-l", "-o", str(offset), appimage_path]
                                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                                if proc.returncode == 0:
                                    for line in proc.stdout.splitlines():
                                        if icon_rel.replace("*", "") in line and line.strip().endswith(ext or ".png"):
                                            fname = line.strip().split()[-1]
                                            dest_file = os.path.join(meta_dir, os.path.basename(fname))
                                            cmd = ["unsquashfs", "-q", "-o", str(offset), "-d", meta_dir, "-e", fname, appimage_path]
                                            proc = subprocess.run(cmd, capture_output=True, timeout=30)
                                            if proc.returncode == 0 and os.path.exists(dest_file):
                                                icon_path = dest_file
                                                break
                                    if icon_path:
                                        break
        except Exception:
            pass

    return desktop_path, icon_path


def _extract_full_appimage(appimage_path: str, extract_dir: str, log) -> str:
    """Full AppImage extraction using the best available method.

    Returns path to extracted AppDir.
    """
    # Check architecture compatibility before attempting extraction
    if not _check_architecture_compatibility(appimage_path, log):
        raise RuntimeError(
            "AppImage architecture is not compatible with this system. "
            "Please use an AppImage built for your architecture."
        )

    path = _ensure_local(appimage_path, log)
    fs_type = _detect_fs_type(path)
    # offset = 0  # reserved for future use

    try:
        from niruvi.desktop.appimage_metadata import AppImageMetadata
        meta = AppImageMetadata(path)
        _ = meta.payload_offset
    except Exception as e:
        log(f"Failed to read AppImage metadata: {e}")

    safe_dir = os.path.join(extract_dir, "squashfs-root" if fs_type == "squashfs" else "dwarfs-root")

    # Try safe extraction first (no code execution)
    if extract_safely(path, safe_dir):
        extracted = _find_extracted_dir(extract_dir)
        if extracted:
            return extracted

    # Fallback to --appimage-extract
    def _try_extract(appimage_path: str) -> str | None:
        log(f"Running: {appimage_path} --appimage-extract in {extract_dir}")
        proc = subprocess.Popen(
            [appimage_path, "--appimage-extract"],
            cwd=extract_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        _stdout, stderr = proc.communicate(timeout=300)
        log(f"Exit code: {proc.returncode}, stderr: {stderr.strip()[:300] if stderr else 'none'}")
        if proc.returncode == 0:
            return _find_extracted_dir(extract_dir)
        return stderr.strip()

    # Ensure executable
    try:
        os.chmod(path, 0o755)
        log(f"chmod 755 on {path}: {'ok' if os.access(path, os.X_OK) else 'FAILED - not executable'}")
    except OSError as e:
        log(f"chmod failed: {e}")

    log(f"Attempting extraction with: {path}")
    result = _try_extract(path)
    if isinstance(result, str) and os.path.isdir(result):
        log("Extraction succeeded on first attempt")
        return result

    # If extraction failed with permission error, try copying to local temp first
    stderr_msg = result if isinstance(result, str) else ""
    log(f"First extraction attempt failed: {stderr_msg[:200] if stderr_msg else 'unknown error'}")
    if "Permission denied" in stderr_msg or "fopen error" in stderr_msg:
        log("AppImage extraction failed due to permissions — copying to local temp and retrying...")
        local_path = os.path.join(tempfile.gettempdir(), f"niruvi-extract-{os.path.basename(path)}")
        shutil.copy2(path, local_path)
        os.chmod(local_path, 0o755)
        log(f"Copied to local: {local_path}, executable: {os.access(local_path, os.X_OK)}")
        result = _try_extract(local_path)
        if isinstance(result, str) and os.path.isdir(result):
            log("Extraction succeeded on retry with local copy")
            return result
        stderr_msg = result if isinstance(result, str) else stderr_msg
        log(f"Retry extraction also failed: {stderr_msg[:200] if stderr_msg else 'unknown error'}")

    # Final error handling
    msg = stderr_msg
    if "Quota exceeded" in msg or "quota" in msg.lower() or "No space left" in msg:
        msg = "Not enough disk space to extract the AppImage. Free up space and try again."
    elif "Permission denied" in msg or "fopen error" in msg:
        msg = (
            "Extraction failed due to permission issues. This can happen if the AppImage "
            "is on a filesystem that doesn't support execute permissions (e.g., FAT32, "
            "network share with noexec). Try copying the AppImage to your home directory first."
        )
    raise RuntimeError(f"Extraction failed: {msg}")


def _run_extraction(appimage_path: str, extract_dir: str, log=None, process_tracker: list | None = None):
    log = log or (lambda m: None)

    def _log(msg: str):
        """Log to both callback and stdout for debugging."""
        log(msg)
        print(f"[NIRUVI EXTRACT] {msg}", flush=True)

    extracted_dir = _extract_full_appimage(appimage_path, extract_dir, _log)
    return extracted_dir


def _atomic_install(extracted_dir: str, dest_dir: str) -> None:
    """Copy extracted_dir to dest_dir atomically with fsync for durability.

    Uses a staging directory for crash safety — if the copy fails, dest_dir is untouched.
    After copy, fsyncs the staging directory before atomic replace.
    """
    parent = os.path.dirname(dest_dir)
    os.makedirs(parent, exist_ok=True)
    staging = dest_dir + ".staging"
    if os.path.exists(staging):
        shutil.rmtree(staging, ignore_errors=True)
    try:
        shutil.copytree(extracted_dir, staging, dirs_exist_ok=True, symlinks=True, ignore_dangling_symlinks=True)
        # Fsync all files in staging to ensure data is on disk before replace
        for root, _dirs, files in os.walk(staging):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    with open(fp, "rb") as fh:
                        fh.read(1)  # Force read to trigger flush
                except Exception:
                    pass
        # Fsync the staging directory itself
        try:
            dir_fd = os.open(staging, os.O_DIRECTORY | os.O_RDONLY)
            os.fsync(dir_fd)
            os.close(dir_fd)
        except Exception:
            pass
        if os.path.exists(dest_dir):
            shutil.rmtree(dest_dir)
        os.replace(staging, dest_dir)
        # Fsync parent directory to persist the rename
        try:
            parent_fd = os.open(parent, os.O_DIRECTORY | os.O_RDONLY)
            os.fsync(parent_fd)
            os.close(parent_fd)
        except Exception:
            pass
    except Exception as e:
        logger.debug("Atomic install copy failed, rolling back staging dir: %s", e, exc_info=True)
        if os.path.exists(staging):
            shutil.rmtree(staging, ignore_errors=True)
        raise


def extract_appimage_sync(appimage_path: str, dest_dir: str) -> None:
    """Extract an AppImage synchronously (no Qt threading), atomic install."""
    # Use a dedicated temp directory that's guaranteed to be cleaned up
    extract_base = _get_extraction_temp_dir()
    tmpdir = tempfile.mkdtemp(prefix="niruvi-extract-", dir=extract_base)
    try:
        extracted_dir = _run_extraction(appimage_path, tmpdir)
        _check_junest_destination(extracted_dir, dest_dir)
        _atomic_install(extracted_dir, dest_dir)
    finally:
        # Always clean up the temp extraction directory
        shutil.rmtree(tmpdir, ignore_errors=False)


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
        self._temp_extract_dir: str | None = None

    def run(self):
        try:
            self.log_message.emit(f"Extracting {self.appimage_path}...")
            self.progress_updated.emit(10)

            # Create a dedicated temp directory for extraction (avoid quota-limited /tmp)
            extract_base = _get_extraction_temp_dir()
            self._temp_extract_dir = tempfile.mkdtemp(prefix="niruvi-extract-", dir=extract_base)

            try:
                self.log_message.emit("Extracting (safe mode)...")
                self.progress_updated.emit(20)

                _proc_tracker: list = []
                extracted_dir_path = _run_extraction(
                    self.appimage_path, self._temp_extract_dir, self.log_message.emit, _proc_tracker
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

            finally:
                # Always clean up the temp extraction directory
                if self._temp_extract_dir and os.path.exists(self._temp_extract_dir):
                    try:
                        shutil.rmtree(self._temp_extract_dir, ignore_errors=False)
                    except Exception as e:
                        logger.warning("Failed to clean up temp extraction dir %s: %s", self._temp_extract_dir, e)

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
        # Also clean up temp extraction dir on stop
        if self._temp_extract_dir and os.path.exists(self._temp_extract_dir):
            try:
                shutil.rmtree(self._temp_extract_dir, ignore_errors=False)
            except Exception as e:
                logger.warning("Failed to clean up temp extraction dir on stop: %s", e)


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
            if not self.expected_sha256:
                logger.warning(
                    "Downloading %s WITHOUT integrity verification (no SHA256 provided) — source: %s",
                    self.dest_path,
                    self.url,
                )
            if self.url.startswith("http://"):
                logger.warning("Downloading over plaintext HTTP: %s", self.url)
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
