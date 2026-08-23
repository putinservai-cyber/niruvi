"""Data integrity verification for Niruvi stored data.

Provides HMAC-based integrity checking for JSON data files
(settings.json, permissions.json) and per-row HMAC signing for the
SQLite installation registry (registry.db) to detect tampering or
corruption.
"""

import hashlib
import hmac
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_HMAC_KEY: str | None = None


def _get_key_path() -> str:
    """Return the path to the per-user HMAC key file.

    Stored in ~/.config/niruvi/ so the same key is used regardless of
    which data directory the app is running from (AppImage, installed, source).
    """
    return os.path.join(os.path.expanduser("~/.config/niruvi"), ".hmac_key")


def _get_hmac_key() -> str:
    """Derive a per-user secret for HMAC signing.

    Generates a random 32-byte key on first use and stores it
    in the user's data directory with 0600 permissions.
    Unlike /etc/machine-id, this is not world-readable.
    """
    global _HMAC_KEY
    if _HMAC_KEY is not None:
        return _HMAC_KEY
    key_path = _get_key_path()
    try:
        with open(key_path) as f:
            _HMAC_KEY = f.read(128).strip()
            if _HMAC_KEY:
                return _HMAC_KEY
    except OSError:
        pass
    import secrets

    _HMAC_KEY = secrets.token_hex(32)
    try:
        os.makedirs(os.path.dirname(key_path), exist_ok=True)
        with os.fdopen(os.open(key_path, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600), "w") as f:
            f.write(_HMAC_KEY)
    except FileExistsError:
        # A keyfile exists but is empty/unreadable-as-written (e.g. created by
        # an interrupted first run). Previously the freshly generated key was
        # used in memory only, invalidating every stored signature on the next
        # start. Rewrite it so the key persists — only when we safely can.
        try:
            import stat as _stat

            st = os.stat(key_path)
            if (
                st.st_uid == os.getuid()
                and os.path.isfile(key_path)
                and not os.path.islink(key_path)
                and _stat.S_ISREG(st.st_mode)
            ):
                with os.fdopen(os.open(key_path, os.O_WRONLY | os.O_TRUNC, 0o600), "w") as f:
                    f.write(_HMAC_KEY)
                    f.flush()
                    os.fsync(f.fileno())
            else:
                logger.warning("HMAC key file %s has unexpected owner/type — key not persisted", key_path)
        except OSError as e:
            logger.warning("Could not persist regenerated HMAC key: %s", e)
    except OSError:
        pass
    return _HMAC_KEY


def sign_data(data: dict) -> str:
    """Create an HMAC signature for a data dictionary."""
    raw = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hmac.new(
        _get_hmac_key().encode(),
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
    try:
        with open(path) as f:
            payload = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload, dict):
        return None
    sig = payload.get("_hmac", "")
    data: dict[str, Any] | None = payload.get("_data")
    if data is not None and sig:
        if verify_data(data, sig):
            return data
        logger.warning("HMAC verification failed for %s — data may be tampered", path)
        return None
    return data if data is not None else payload
