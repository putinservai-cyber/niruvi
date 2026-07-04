"""Data integrity verification for Niruvi stored data.

Provides HMAC-based integrity checking for JSON data files
(settings.json, registry.json, permissions.json) to detect
tampering or corruption.
"""

import hashlib
import hmac
import json
import logging
import os

logger = logging.getLogger(__name__)

_MACHINE_SECRET: str | None = None


def _get_machine_secret() -> str:
    """Derive a machine-local secret for HMAC signing.

    Uses /etc/machine-id (or D-Bus machine ID) as the seed.
    This is consistent per-machine but not portable across machines.
    """
    global _MACHINE_SECRET
    if _MACHINE_SECRET is not None:
        return _MACHINE_SECRET
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            with open(path) as f:
                mid = f.read(64).strip()
                if mid:
                    _MACHINE_SECRET = hashlib.sha256(mid.encode()).hexdigest()
                    return _MACHINE_SECRET
        except OSError:
            continue
    _MACHINE_SECRET = hashlib.sha256(os.uname().nodename.encode()).hexdigest()
    return _MACHINE_SECRET


def sign_data(data: dict) -> str:
    """Create an HMAC signature for a data dictionary."""
    raw = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hmac.new(
        _get_machine_secret().encode(),
        raw.encode(),
        hashlib.sha256,
    ).hexdigest()


def verify_data(data: dict, signature: str) -> bool:
    """Verify an HMAC signature against a data dictionary."""
    expected = sign_data(data)
    return hmac.compare_digest(expected, signature)


def write_json_with_hmac(path: str, data: dict):
    """Write JSON data with an embedded HMAC signature."""
    sig = sign_data(data)
    payload = {
        "_hmac": sig,
        "_data": data,
    }
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(payload, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except OSError:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def read_json_with_hmac(path: str) -> dict | None:
    """Read and verify JSON data with embedded HMAC signature.

    Returns the data dict if valid, or None if tampered/corrupted.
    """
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            payload = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload, dict):
        return None
    sig = payload.get("_hmac", "")
    data = payload.get("_data")
    if not data or not sig:
        return data
    if verify_data(data, sig):
        return data
    logger.warning("HMAC verification failed for %s — data may be tampered", path)
    return None
