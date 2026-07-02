"""Signing Engine — GPG signing and key management for AppImage builds.

Provides functions to sign AppImages with GPG detached signatures,
manage GPG keys, and verify signatures. Integrates with the manifest
system and the build pipeline.
"""

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)


class SigningKey(NamedTuple):
    fingerprint: str
    name: str
    email: str
    can_sign: bool


class SigningError(Exception):
    """Raised when signing operations fail."""


def gpg_available() -> bool:
    """Check if GPG is installed and usable."""
    return shutil.which("gpg") is not None


def list_secret_keys() -> list[SigningKey]:
    """List all available secret (signing) GPG keys."""
    if not gpg_available():
        return []
    try:
        result = subprocess.run(
            ["gpg", "--list-secret-keys", "--with-colons"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return []
        keys: list[SigningKey] = []
        current_fp = ""
        current_name = ""
        current_email = ""
        for line in result.stdout.splitlines():
            parts = line.split(":")
            if not parts:
                continue
            record_type = parts[0]
            if record_type == "fpr":
                current_fp = parts[9] if len(parts) > 9 else ""
            elif record_type == "uid":
                uid = parts[9] if len(parts) > 9 else ""
                import re
                m = re.match(r"^(.*?)\s*<(.+?)>\s*$", uid)
                if m:
                    current_name = m.group(1).strip()
                    current_email = m.group(2).strip()
                else:
                    current_name = uid.strip()
                    current_email = ""
            elif record_type == "sec":
                can_sign = parts[1] in ("u", "s")
                if current_fp and current_name:
                    keys.append(SigningKey(current_fp, current_name, current_email, can_sign))
                current_fp = ""
                current_name = ""
                current_email = ""
        return keys
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("Failed to list GPG keys: %s", e)
        return []


def sign_file(path: str, key_fingerprint: str | None = None,
              output_sig: str | None = None, armor: bool = True) -> str:
    """Create a GPG detached signature for a file.

    Args:
        path: Path to the file to sign.
        key_fingerprint: GPG key fingerprint to sign with. If None, uses default key.
        output_sig: Output path for the signature file. If None, appends '.sig'.

    Returns:
        Path to the created signature file.

    Raises:
        SigningError: If GPG is not available or signing fails.
    """
    if not gpg_available():
        raise SigningError("GPG is not available")
    if not os.path.isfile(path):
        raise SigningError(f"File not found: {path}")
    if output_sig is None:
        output_sig = path + ".sig"
    cmd = ["gpg", "--detach-sign"]
    if armor:
        cmd.append("--armor")
    if key_fingerprint:
        cmd.extend(["--local-user", key_fingerprint])
    cmd.extend(["--output", output_sig, path])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise SigningError(f"GPG signing failed: {result.stderr.strip()}")
        logger.info("Signed %s -> %s with key %s", path, output_sig,
                    key_fingerprint or "default")
        return output_sig
    except FileNotFoundError as e:
        raise SigningError(f"GPG not found: {e}") from e
    except subprocess.TimeoutExpired:
        raise SigningError("GPG signing timed out")


def sign_appimage(appimage_path: str, key_fingerprint: str | None = None,
                  armor: bool = True) -> str:
    """Sign an AppImage file with a GPG detached signature.

    The signature is placed alongside the AppImage as <name>.AppImage.sig.

    Args:
        appimage_path: Path to the AppImage file.

    Returns:
        Path to the created signature file.
    """
    return sign_file(appimage_path, key_fingerprint, armor=armor)


def verify_signature(path: str, sig_path: str | None = None,
                     gpg_keyring: str | None = None) -> bool:
    """Verify a GPG detached signature against a file.

    Args:
        path: Path to the signed file.
        sig_path: Path to the signature file. If None, appends '.sig'.
        gpg_keyring: Optional path to a GPG keyring for verification.

    Returns:
        True if the signature is valid, False otherwise.
    """
    if not gpg_available():
        return False
    if sig_path is None:
        sig_path = path + ".sig"
    if not os.path.isfile(sig_path):
        return False
    cmd = ["gpg", "--verify"]
    if gpg_keyring:
        cmd.extend(["--keyring", gpg_keyring])
    cmd.extend([sig_path, path])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def export_public_key(fingerprint: str, output_path: str,
                      armor: bool = True) -> str:
    """Export a GPG public key to a file.

    Args:
        fingerprint: GPG key fingerprint to export.
        output_path: Path to write the exported key.

    Returns:
        Path to the exported key file.

    Raises:
        SigningError: If export fails.
    """
    if not gpg_available():
        raise SigningError("GPG is not available")
    cmd = ["gpg", "--export"]
    if armor:
        cmd.append("--armor")
    cmd.extend(["--output", output_path, fingerprint])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            raise SigningError(f"Failed to export key: {result.stderr.strip()}")
        return output_path
    except FileNotFoundError as e:
        raise SigningError(f"GPG not found: {e}") from e
    except subprocess.TimeoutExpired:
        raise SigningError("GPG export timed out")


def import_public_key(key_path: str) -> str:
    """Import a GPG public key from a file.

    Args:
        key_path: Path to the key file to import.

    Returns:
        Fingerprint of the imported key.

    Raises:
        SigningError: If import fails.
    """
    if not gpg_available():
        raise SigningError("GPG is not available")
    if not os.path.isfile(key_path):
        raise SigningError(f"Key file not found: {key_path}")
    cmd = ["gpg", "--import", key_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            raise SigningError(f"Failed to import key: {result.stderr.strip()}")
        fp = _extract_fingerprint_from_import(result.stderr)
        if fp:
            logger.info("Imported GPG key: %s", fp)
        return fp or ""
    except FileNotFoundError as e:
        raise SigningError(f"GPG not found: {e}") from e
    except subprocess.TimeoutExpired:
        raise SigningError("GPG import timed out")


def _extract_fingerprint_from_import(stderr: str) -> str:
    """Extract fingerprint from 'gpg --import' stderr output."""
    for line in stderr.splitlines():
        line = line.strip()
        if "fingerprint" in line.lower():
            if "=" in line:
                fp = line.split("=", 1)[-1].strip()
            elif ":" in line:
                fp = line.split(":", 1)[-1].strip()
            else:
                continue
            return fp.replace(" ", "")
    return ""


def get_default_key() -> SigningKey | None:
    """Get the default GPG signing key, if any."""
    keys = list_secret_keys()
    for k in keys:
        if k.can_sign:
            return k
    return None


def signing_info_for_manifest(fingerprint: str, sig_path: str) -> dict:
    """Build the signing info dict for inclusion in a Manifest.

    Args:
        fingerprint: GPG key fingerprint used for signing.
        sig_path: Path to the signature file.

    Returns:
        Dict suitable for ``Manifest`` ``signing`` field.
    """
    import hashlib
    sig_sha256 = ""
    if os.path.isfile(sig_path):
        h = hashlib.sha256()
        with open(sig_path, "rb") as f:
            while True:
                data = f.read(65536)
                if not data:
                    break
                h.update(data)
        sig_sha256 = h.hexdigest()
    return {
        "algorithm": "gpg",
        "fingerprint": fingerprint,
        "signature": sig_sha256,
        "timestamp": "",
    }
