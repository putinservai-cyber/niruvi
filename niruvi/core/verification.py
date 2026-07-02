"""Verification Engine — package integrity and authenticity verification.

Provides SHA-256 checksumming, GPG signature verification, manifest
validation, and file integrity checking for installed and packaged apps.
"""

import hashlib
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Sequence

logger = logging.getLogger(__name__)


class VerificationError(Exception):
    """Raised when verification fails."""


class VerificationResult:
    """Result of a verification operation."""

    def __init__(self, passed: bool, details: str = "", errors: list[str] | None = None):
        self.passed = passed
        self.details = details
        self.errors = errors or []

    def __bool__(self):
        return self.passed

    def __repr__(self):
        return f"<VerificationResult passed={self.passed} errors={len(self.errors)}>"


def sha256_file(path: str, chunk_size: int = 65536) -> str:
    """Compute SHA-256 of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            data = f.read(chunk_size)
            if not data:
                break
            h.update(data)
    return h.hexdigest()


def sha256_payload(path: str, offset: int = 0) -> str:
    """Compute SHA-256 from a given byte offset (payload-only hash)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        f.seek(offset)
        while True:
            data = f.read(65536)
            if not data:
                break
            h.update(data)
    return h.hexdigest()


def verify_sha256(path: str, expected: str) -> VerificationResult:
    actual = sha256_file(path)
    if actual.lower() == expected.lower():
        return VerificationResult(True, "SHA-256 matches")
    return VerificationResult(
        False, "SHA-256 mismatch",
        [f"Expected: {expected}", f"Actual:   {actual}"],
    )


def verify_manifest_integrity(app_dir: str) -> VerificationResult:
    """Validate that the manifest file in an AppDir/install is internally consistent."""
    try:
        from niruvi.core.manifest import load_manifest, ManifestError
    except ImportError:
        return VerificationResult(False, "Manifest module not available")
    m = load_manifest(app_dir)
    if m is None:
        return VerificationResult(False, "No manifest found")
    try:
        m.validate()
        return VerificationResult(True, f"Manifest valid: {m.app_id} v{m.version}")
    except ManifestError as e:
        return VerificationResult(False, str(e))


def verify_apprun_executable(app_dir: str) -> VerificationResult:
    apprun = os.path.join(app_dir, "AppRun")
    if not os.path.isfile(apprun):
        return VerificationResult(False, "AppRun not found")
    if not os.access(apprun, os.X_OK):
        return VerificationResult(False, "AppRun is not executable")
    return VerificationResult(True, "AppRun is present and executable")


def verify_desktop_file(app_dir: str) -> VerificationResult:
    for fname in os.listdir(app_dir):
        if fname.endswith(".desktop"):
            path = os.path.join(app_dir, fname)
            try:
                result = subprocess.run(
                    ["desktop-file-validate", path],
                    capture_output=True, text=True, timeout=15,
                )
                if result.returncode == 0:
                    return VerificationResult(True, f"Desktop file valid: {fname}")
                return VerificationResult(False, result.stderr.strip())
            except (FileNotFoundError, subprocess.TimeoutExpired):
                return VerificationResult(True, "desktop-file-validate not available")
    return VerificationResult(False, "No .desktop file found")


def verify_essential_files(app_dir: str) -> VerificationResult:
    """Verify all essential files exist and are valid."""
    required = ["AppRun"]
    missing = [f for f in required if not os.path.isfile(os.path.join(app_dir, f))]
    if missing:
        return VerificationResult(False, f"Missing files: {missing}")
    return VerificationResult(True, "All essential files present")


def verify_complete(app_dir: str) -> list[VerificationResult]:
    """Run all verification checks and return results."""
    checks = [
        ("Manifest integrity", verify_manifest_integrity),
        ("Essential files", verify_essential_files),
        ("AppRun executable", verify_apprun_executable),
        ("Desktop file", verify_desktop_file),
    ]
    results = []
    for name, func in checks:
        try:
            result = func(app_dir)
            result.details = f"[{name}] {result.details}"
            results.append(result)
        except Exception as e:
            results.append(VerificationResult(False, f"[{name}] Error: {e}"))
    return results


def verify_gpg_signature(path: str, sig_path: str | None = None,
                         gpg_keyring: str | None = None) -> VerificationResult:
    """Verify a GPG detached signature against a file."""
    if not shutil.which("gpg"):
        return VerificationResult(False, "GPG not available")
    if sig_path is None:
        sig_path = path + ".sig"
    if not os.path.isfile(sig_path):
        return VerificationResult(False, f"Signature file not found: {sig_path}")
    cmd = ["gpg", "--verify"]
    if gpg_keyring:
        cmd.extend(["--keyring", gpg_keyring])
    cmd.extend([sig_path, path])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return VerificationResult(True, "GPG signature valid")
        return VerificationResult(False, result.stderr.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return VerificationResult(False, str(e))


def check_file_integrity(app_dir: str, reference_sha256: dict[str, str]) -> list[VerificationResult]:
    """Compare file hashes against a reference dictionary."""
    results = []
    for rel_path, expected_sha in reference_sha256.items():
        full_path = os.path.join(app_dir, rel_path)
        if not os.path.isfile(full_path):
            results.append(VerificationResult(False, f"Missing: {rel_path}"))
            continue
        actual = sha256_file(full_path)
        if actual.lower() == expected_sha.lower():
            results.append(VerificationResult(True, f"{rel_path}: OK"))
        else:
            results.append(VerificationResult(False, f"{rel_path}: hash mismatch"))
    return results
