"""Safe AppImage extraction — extract without executing code."""

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from niruvi.desktop.appimage_metadata import AppImageMetadata


def _extract_dwarfs(appimage_path: str, offset: int, dest: str) -> bool:
    """Extract a DwarFS AppImage using dwarfsextract."""
    if not shutil.which("dwarfsextract"):
        return False
    try:
        proc = subprocess.Popen(
            ["dwarfsextract", "-i", appimage_path, "-o", dest, "-O", str(offset)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        proc.communicate(timeout=120)
        return proc.returncode == 0 and os.path.isdir(dest) and os.listdir(dest)
    except Exception:
        return False


def _extract_squashfs(appimage_path: str, offset: int, dest: str) -> bool:
    """Extract a SquashFS AppImage using unsquashfs."""
    if not shutil.which("unsquashfs"):
        return False
    try:
        proc = subprocess.Popen(
            ["unsquashfs", "-d", dest, "-offset", str(offset), "-force", appimage_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        proc.communicate(timeout=120)
        if proc.returncode == 0 and os.path.isdir(dest) and os.listdir(dest):
            return True
    except Exception:
        pass
    try:
        with tempfile.NamedTemporaryFile(suffix=".squashfs", delete=False) as tmp:
            squash_path = tmp.name
        subprocess.run(
            ["dd", f"skip={offset}", "iflag=skip_bytes", f"if={appimage_path}", f"of={squash_path}"],
            capture_output=True, timeout=30, check=True,
        )
        proc = subprocess.Popen(
            ["unsquashfs", "-d", dest, "-force", squash_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        proc.communicate(timeout=120)
        Path(squash_path).unlink(missing_ok=True)
        if proc.returncode == 0 and os.path.isdir(dest) and os.listdir(dest):
            return True
    except Exception:
        Path(squash_path).unlink(missing_ok=True)
    return False


def extract_safely(appimage_path: str, dest: str) -> bool:
    """Extract an AppImage without executing its code.

    Detects the embedded filesystem type (SquashFS or DwarFS) and
    extracts accordingly using unsquashfs or dwarfsextract.
    Returns True if extraction succeeded.
    """
    try:
        meta = AppImageMetadata(appimage_path)
        offset = meta.payload_offset
        fs_type = getattr(meta, 'fs_type', 'squashfs')
    except Exception:
        return False

    if fs_type == 'dwarfs':
        return _extract_dwarfs(appimage_path, offset, dest)
    return _extract_squashfs(appimage_path, offset, dest)
