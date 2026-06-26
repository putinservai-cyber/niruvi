"""Security scanner for AppImage files.

Performs static analysis on AppImage files to detect potential
security risks including malware, suspicious scripts, and setuid binaries.
"""

import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from niruvi.appimage_metadata import AppImageMetadata

# Suspicion is based on script content matching SUSPICIOUS_SCRIPT_PATTERNS.
# Files matching these extensions are scanned for suspicious content regardless of extension.
SCAN_ANYWAY_EXTENSIONS = [
    ".sh", ".bash", ".zsh",
    ".pl", ".rb",
]

SUSPICIOUS_FILENAMES = [
    "kexec",
    "sensor-setup",
    "coinminer",
    "xmrig",
    "cryptominer",
    "keylogger",
]

SUSPICIOUS_SCRIPT_PATTERNS = [
    b"wget http://",
    b"wget https://",
    b"curl http://",
    b"curl https://",
    b"chmod 777 /",
    b"chmod -R 777 /",
    b"> /dev/sda",
    b"| bash -c",
    b"| sh -c",
    b"/dev/tcp/",
    b"base64 --decode",
    b"curl -s http://",
    b"wget -q http://",
    b"chmod 4777",
    b"pkexec",
    b"sudo rm -rf /",
    b"dd if=/dev/",
    b":(){ :|:& };:",   # fork bomb
    b"wget -O-",
    b"curl -o-",
    b"chmod -R 777 /",
    b"exec 5<>/dev/tcp/",
]


def scan_appimage(path: str) -> dict:
    """Run all security checks on an AppImage file.

    Returns a dict with keys:
        risk_level (str): safe / low / medium / high
        sha256 (str): SHA256 of the payload
        size_mb (float): file size in MB
        is_valid_appimage (bool)
        warnings (list[str]): human-readable warning messages
        details (dict): raw scan data
    """
    result = {
        "risk_level": "safe",
        "sha256": "",
        "size_mb": 0.0,
        "is_valid_appimage": False,
        "warnings": [],
        "details": {},
    }

    path_str = str(Path(path).resolve())

    if not os.path.isfile(path_str):
        result["risk_level"] = "high"
        result["warnings"].append("File not found")
        return result

    size = os.path.getsize(path_str)
    result["size_mb"] = size / (1024 * 1024)

    if result["size_mb"] > 500:
        result["warnings"].append(f"Large file ({result['size_mb']:.0f} MB) — possible data smuggling")
        result["risk_level"] = "low"

    if result["size_mb"] < 0.1:
        result["warnings"].append(f"Very small file ({result['size_mb']:.1f} MB) — not a valid AppImage")
        result["risk_level"] = "medium"
        return result

    try:
        meta = AppImageMetadata(path_str)
        result["is_valid_appimage"] = True
        result["sha256"] = meta.sha256
        result["details"]["architecture"] = meta.architecture
        result["details"]["type"] = meta.type
        result["details"]["filesystem"] = meta.fs_type
    except ValueError as e:
        result["warnings"].append(f"Not a valid AppImage: {e}")
        result["risk_level"] = "high"
        return result

    contents = _scan_contents(path_str)
    result["details"].update(contents)

    if contents.get("setuid_binaries"):
        for suid in contents["setuid_binaries"]:
            result["warnings"].append(f"Setuid binary found: {suid}")
        result["risk_level"] = max(result["risk_level"], "high", key=_risk_order)

    if contents.get("world_writable_files"):
        for wf in contents["world_writable_files"]:
            result["warnings"].append(f"World-writable file: {wf}")
        if result["risk_level"] == "safe":
            result["risk_level"] = "low"

    if contents.get("suspicious_scripts"):
        for script in contents["suspicious_scripts"]:
            result["warnings"].append(f"Suspicious script: {script}")
        result["risk_level"] = max(result["risk_level"], "medium", key=_risk_order)

    if contents.get("suspicious_filenames"):
        for fname in contents["suspicious_filenames"]:
            result["warnings"].append(f"Suspicious filename: {fname}")
        result["risk_level"] = max(result["risk_level"], "high", key=_risk_order)

    clamav = _check_clamav(path_str)
    if clamav:
        result["details"]["clamav"] = clamav
        if clamav.get("infected"):
            result["warnings"].extend(clamav.get("warnings", []))
            result["risk_level"] = "high"

    return result


def _risk_order(level: str) -> int:
    return {"safe": 0, "low": 1, "medium": 2, "high": 3}.get(level, 0)


def extract_safely(appimage_path: str, dest: str) -> bool:
    """Extract an AppImage without executing its code.
    
    Uses the ELF header offset to extract the embedded squashfs filesystem
    directly via unsquashfs or manual dd+unsquashfs pipeline.
    Returns True if extraction succeeded.
    """
    try:
        meta = AppImageMetadata(appimage_path)
        offset = meta.payload_offset
    except Exception:
        return False

    # Try unsquashfs with offset (does not execute the AppImage)
    if shutil.which("unsquashfs"):
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

    # Fallback: extract via dd + unsquashfs pipeline
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


def _scan_contents(appimage_path: str) -> dict:
    """Extract and scan the contents of an AppImage for suspicious files.
    
    Uses ELF header offset to extract the squashfs directly without
    executing the AppImage binary (avoids running untrusted code).
    """
    result = {
        "setuid_binaries": [],
        "world_writable_files": [],
        "suspicious_scripts": [],
        "suspicious_filenames": [],
    }

    try:
        with tempfile.TemporaryDirectory(prefix="niruvi-scan-") as tmp:
            extracted = os.path.join(tmp, "squashfs-root")
            if not extract_safely(appimage_path, extracted):
                result["suspicious_scripts"].append(
                    "Could not extract AppImage contents for scanning — unsquashfs not available"
                )
                return result

            for root, dirs, files in os.walk(extracted):
                try:
                    for f in files:
                        fpath = os.path.join(root, f)
                        rel = os.path.relpath(fpath, extracted)

                        try:
                            st = os.lstat(fpath)
                            mode = st.st_mode

                            if st.st_uid == 0 and (mode & 0o4000):
                                result["setuid_binaries"].append(rel)

                            if mode & 0o0002:
                                result["world_writable_files"].append(rel)

                        except OSError:
                            continue

                        ext = os.path.splitext(f)[1].lower()
                        if ext in SCAN_ANYWAY_EXTENSIONS or not ext:
                            try:
                                if os.path.getsize(fpath) < 1024 * 1024:
                                    with open(fpath, "rb") as fh:
                                        content = fh.read()
                                    for pattern in SUSPICIOUS_SCRIPT_PATTERNS:
                                        if pattern in content:
                                            result["suspicious_scripts"].append(rel)
                                            break
                            except OSError:
                                continue

                        fname_lower = f.lower()
                        for suspicious_name in SUSPICIOUS_FILENAMES:
                            if suspicious_name in fname_lower:
                                result["suspicious_filenames"].append(rel)
                                break

                except OSError:
                    continue

    except subprocess.TimeoutExpired:
        result["suspicious_scripts"].append("Extraction timed out during scan")
    except Exception as e:
        logging.debug("Content scan failed: %s", e)

    return result
def self_scan() -> dict:
    """Run a security scan on Niruvi itself (if running from AppImage).

    Returns the scan result dict, or a dict with error info if not running as AppImage.
    """
    appimage = os.environ.get("APPIMAGE", "")
    if not appimage or not os.path.isfile(appimage):
        return {
            "risk_level": "safe",
            "warnings": ["Niruvi is not running from an AppImage — self-scan not applicable."],
            "details": {},
        }
    return scan_appimage(appimage)




def _check_clamav(filepath: str) -> dict | None:
    """Run ClamAV scan if clamscan is available."""
    if not shutil.which("clamscan"):
        return None

    try:
        proc = subprocess.Popen(
            ["clamscan", "--no-summary", "--stdout", filepath],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = proc.communicate(timeout=120)

        if proc.returncode == 1:
            lines = [l.strip() for l in stdout.split("\n") if l.strip() and "FOUND" in l.upper()]
            return {
                "infected": True,
                "warnings": [f"ClamAV: {l}" for l in lines] if lines else ["ClamAV detected a threat"],
            }
        elif proc.returncode != 0:
            return {
                "infected": False,
                "warnings": [f"ClamAV error: {stderr.strip() or proc.returncode}"],
            }
        return {"infected": False, "warnings": []}

    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return {"infected": False, "warnings": ["ClamAV scan timed out"]}
    except Exception as e:
        logging.debug("ClamAV scan failed: %s", e)
        return None
