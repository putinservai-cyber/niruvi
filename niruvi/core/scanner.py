"""Safe AppImage extraction — extract without executing code."""

import logging
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

from niruvi.desktop.appimage_metadata import AppImageMetadata

logger = logging.getLogger(__name__)

SUSPICIOUS_PATTERNS: list[tuple[str, str, str]] = [
    ("reverse_shell", r"(\/dev\/tcp\/|\/dev\/udp\/|bash\s+-i\s+>&\s+/dev/tcp|sh\s+-i\s+>&\s+/dev/tcp)"),
    ("crypto_miner", r"(stratum\+tcp|monero|cryptonight|xmrig|cgminer)"),
    ("sudo_exploit", r"(sudo\s+chmod\s+4777|pkexec\s+--user\s+root|CVE-\d{4}-\d{4,})"),
    ("persistence", r"(cron\s+@reboot|systemctl\s+enable\s+|\.config/autostart|rc\.local)"),
    ("known_malware", r"(mirai|bot\.py|reverse_shell\.py|trojan|keylogger|ransom)"),
    ("process_injection", r"(ptrace\s*\(|process_vm_readv|process_vm_writev)"),
    ("data_exfil", r"(curl\s+-\s+.*\d+\.\d+\.\d+\.\d+|wget\s+.*\d+\.\d+\.\d+\.\d+|nc\s+.*-e\s+/bin)"),
]

SUSPICIOUS_EXTENSIONS = {".exe", ".dll", ".com", ".bat", ".ps1", ".vbs", ".scr"}


def scan_file(path: str) -> dict:
    """Scan a single file for suspicious patterns. Returns {path, verdict, matches}."""
    result: dict = {"path": path, "verdict": "clean", "matches": []}
    try:
        st = os.stat(path)
    except OSError:
        return result
    if st.st_mode & (stat.S_ISUID | stat.S_ISGID):
        result["matches"].append("setuid/setgid binary")
    ext = os.path.splitext(path)[1].lower()
    if ext in SUSPICIOUS_EXTENSIONS:
        result["matches"].append(f"Windows executable in AppImage: {ext}")
    try:
        with open(path, "rb") as f:
            head = f.read(1024 * 1024)
            try:
                text_head = head.decode("utf-8", errors="replace")
            except Exception:
                text_head = ""
            text_tail = ""
            if st.st_size > 2 * 1024 * 1024:
                try:
                    f.seek(max(0, st.st_size - 1024 * 1024))
                    tail = f.read(1024 * 1024)
                    text_tail = tail.decode("utf-8", errors="replace") if tail else ""
                except Exception:
                    pass
            text = text_head + text_tail
            for name, pattern in SUSPICIOUS_PATTERNS:
                if re.search(pattern, text, re.IGNORECASE):
                    result["matches"].append(name)
    except (OSError, MemoryError):
        pass
    if result["matches"]:
        result["verdict"] = "suspicious"
    return result


def scan_directory(app_dir: str) -> list[dict]:
    """Recursively scan an extracted AppDir for suspicious content."""
    results: list[dict] = []
    script_exts = {".sh", ".py", ".pl", ".rb", ".js", ".php", ".lua"}
    for root, _dirs, files in os.walk(app_dir):
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext in script_exts or (ext not in (".png", ".jpg", ".svg", ".ico", ".desktop", ".sig", ".blockmap")):
                full = os.path.join(root, fname)
                scan_result = scan_file(full)
                if scan_result["verdict"] == "suspicious":
                    results.append(scan_result)
    return results


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
    except Exception as e:
        logger.debug("DwarFS extraction failed for %s: %s", appimage_path, e, exc_info=True)
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
    except Exception as e:
        logger.debug("SquashFS direct extraction failed for %s: %s", appimage_path, e, exc_info=True)
    try:
        with tempfile.NamedTemporaryFile(suffix=".squashfs", delete=False) as tmp:
            squash_path = tmp.name
        subprocess.run(
            ["dd", f"skip={offset}", "iflag=skip_bytes", f"if={appimage_path}", f"of={squash_path}"],
            capture_output=True,
            timeout=30,
            check=True,
        )
        proc = subprocess.Popen(
            ["unsquashfs", "-d", dest, "-force", squash_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        proc.communicate(timeout=120)
        Path(squash_path).unlink(missing_ok=True)
        if proc.returncode == 0 and os.path.isdir(dest) and os.listdir(dest):
            return True
    except Exception as e:
        logger.debug("SquashFS dd fallback extraction failed for %s: %s", appimage_path, e, exc_info=True)
        Path(squash_path).unlink(missing_ok=True)
    return False


def _strip_setuid(path: str):
    """Remove SUID/SGID bits from an extracted file."""
    # Skip broken symlinks and vanished files — they can't carry SUID bits
    if not os.path.exists(path):
        return
    if os.path.islink(path):
        return
    try:
        st = os.stat(path)
        if st.st_mode & (stat.S_ISUID | stat.S_ISGID):
            new_mode = st.st_mode & ~(stat.S_ISUID | stat.S_ISGID)
            os.chmod(path, new_mode)
            logger.debug("Stripped SUID/SGID from %s", path)
    except OSError as e:
        logger.debug("Failed to strip SUID/SGID from %s: %s", path, e)


def _strip_all_setuid(app_dir: str):
    """Recursively strip SUID/SGID from all files in extracted directory."""
    for root, _dirs, files in os.walk(app_dir):
        for fname in files:
            _strip_setuid(os.path.join(root, fname))


def extract_safely(appimage_path: str, dest: str, strip_suid: bool = True) -> bool:
    """Extract an AppImage without executing its code.

    Detects the embedded filesystem type (SquashFS or DwarFS) and
    extracts accordingly using unsquashfs or dwarfsextract.
    Returns True if extraction succeeded.
    """
    try:
        meta = AppImageMetadata(appimage_path)
        offset = meta.payload_offset
        fs_type = getattr(meta, "fs_type", "squashfs")
    except Exception as e:
        logger.debug("Failed to read AppImage metadata for %s: %s", appimage_path, e, exc_info=True)
        return False

    result = False
    if fs_type == "dwarfs":
        result = _extract_dwarfs(appimage_path, offset, dest)
    else:
        result = _extract_squashfs(appimage_path, offset, dest)

    if result and strip_suid:
        _strip_all_setuid(dest)

    return result
