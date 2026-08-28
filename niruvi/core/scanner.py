"""Safe AppImage extraction — extract without executing code.

Provides static security analysis of AppImage contents without executing
the AppImage binary. Uses pattern matching and file inspection to
identify potential security issues.
"""

import logging
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

#: Patterns for suspicious content detection
SUSPICIOUS_PATTERNS: list[tuple[str, str]] = [
    # (category, regex_pattern, description)
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
    """Scan a single file for suspicious patterns.

    Examines the file's metadata and content for known malicious patterns,
    setuid/setgid bits, and suspicious Windows extensions.

    Args:
        path: Path to the file to scan.

    Returns:
        dict with keys:
            - path: The file path
            - verdict: "clean" or "suspicious"
            - matches: List of matched pattern categories
    """
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
    """Recursively scan an extracted AppDir for suspicious content.

    Walks the entire AppDirectory tree (nested directories and symlinks
    included) and scans each non-trivial file for suspicious patterns.
    Only scans files with suspicious extensions or those that could
    contain executable code.

    Args:
        app_dir: Path to the AppDirectory to scan.

    Returns:
        List of scan results for files deemed suspicious.
        Each result is a dict with keys: path, verdict, matches.
    """
    results: list[dict] = []
    script_exts = {".sh", ".py", ".pl", ".rb", ".js", ".php", ".lua"}
    skip_exts = {".png", ".jpg", ".svg", ".ico", ".desktop", ".sig", ".blockmap"}
    for root, _dirs, files in os.walk(app_dir, followlinks=False):
        for fname in files:
            fpath = os.path.join(root, fname)
            if os.path.islink(fpath) and not os.path.exists(fpath):
                continue  # broken symlink
            ext = os.path.splitext(fname)[1].lower()
            if ext in script_exts or ext not in skip_exts:
                scan_result = scan_file(fpath)
                if scan_result["verdict"] == "suspicious":
                    results.append(scan_result)
    return results


def _strip_setuid(path: str) -> None:
    """Remove SUID/SGID bits from an extracted file.

    Skips broken symlinks and vanished files since they cannot carry
    SUID/SGID bits on Linux systems.

    Args:
        path: Path to the file from which to strip SUID/SGID bits.
    """
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
    except OSError:
        pass


def _strip_all_setuid(app_dir: str) -> None:
    """Recursively strip SUID/SGID from all files in extracted directory.

    Walks the directory tree and strips SUID/SGID bits from all files,
    helping prevent privilege escalation vulnerabilities.

    Args:
        app_dir: Path to the directory from which to strip SUID/SGID bits.
    """
    for root, _dirs, files in os.walk(app_dir):
        for fname in files:
            _strip_setuid(os.path.join(root, fname))


def _extract_squashfs(appimage_path: str, offset: int, dest: str) -> bool:
    """Extract a SquashFS payload using unsquashfs.

    Args:
        appimage_path: Path to the AppImage file.
        offset: Byte offset of the SquashFS payload within the file.
        dest: Destination directory for the extracted files.

    Returns:
        True if extraction succeeded, False otherwise.
    """
    if not shutil.which("unsquashfs"):
        logger.debug("unsquashfs not available; cannot extract SquashFS payload")
        return False
    try:
        proc = subprocess.run(
            ["unsquashfs", "-q", "-o", str(offset), "-d", dest, appimage_path],
            capture_output=True,
            timeout=120,
        )
        return proc.returncode == 0
    except Exception as e:
        logger.debug("unsquashfs extraction failed for %s: %s", appimage_path, e, exc_info=True)
        return False


def _extract_dwarfs(appimage_path: str, offset: int, dest: str) -> bool:
    """Extract a DwarFS payload using dwarfsextract.

    Args:
        appimage_path: Path to the AppImage file.
        offset: Byte offset of the DwarFS payload within the file.
        dest: Destination directory for the extracted files.

    Returns:
        True if extraction succeeded, False otherwise.
    """
    if not shutil.which("dwarfsextract"):
        logger.debug("dwarfsextract not available; cannot extract DwarFS payload")
        return False
    try:
        proc = subprocess.run(
            ["dwarfsextract", "-o", str(offset), "-d", dest, appimage_path],
            capture_output=True,
            timeout=120,
        )
        return proc.returncode == 0
    except Exception as e:
        logger.debug("dwarfsextract extraction failed for %s: %s", appimage_path, e, exc_info=True)
        return False


def extract_safely(appimage_path: str, dest: str, strip_suid: bool = True) -> bool:
    """Extract an AppImage without executing its code.

    Detects the embedded filesystem type (SquashFS or DwarFS) and
    extracts accordingly using unsquashfs or dwarfsextract.
    Optionally strips SUID/SGID bits from extracted files.

    Args:
        appimage_path: Path to the AppImage file to extract.
        dest: Destination directory for the extracted files.
        strip_suid: If True, strip SUID/SGID bits from extracted files.

    Returns:
        True if extraction succeeded, False otherwise.
    """
    try:
        from niruvi.desktop.appimage_metadata import AppImageMetadata

        meta = AppImageMetadata(appimage_path)
        offset = meta.payload_offset
        fs_type = getattr(meta, "fs_type", "squashfs")
    except Exception as e:
        logger.debug("Failed to read AppImage metadata for %s: %s", appimage_path, e, exc_info=True)
        return False

    result: bool = False
    if fs_type == "dwarfs":
        result = _extract_dwarfs(appimage_path, offset, dest)
    else:
        result = _extract_squashfs(appimage_path, offset, dest)

    if result and strip_suid:
        _strip_all_setuid(dest)

    return result
