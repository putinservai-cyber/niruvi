"""Sandbox audit log: append-only, bounded, 0600.

Records every sandbox launch/refusal with reason and effective profile so a
user can answer "what ran, with what permissions, and when" — the promise in
the brief.  The log is bounded (rotated at _MAX_BYTES) and mode 0600; a lost
or corrupt log must never break a launch ('fail-safe' for usability of the
audit trail only — never for enforcement decisions).
"""

import json
import os
import stat
import threading
import time

_log_lock = threading.Lock()

_MAX_BYTES = 256 * 1024
_KEEP = 1

DEFAULT_LOG = os.path.join("logs", "sandbox_audit.log")


def _log_path(base_dir: str | None = None) -> str:
    if base_dir is None:
        from niruvi.config import get_data_dir

        return os.path.join(get_data_dir(), "logs", "sandbox_audit.log")
    return os.path.join(base_dir, "sandbox_audit.log")


def record_launch(
    app_id: str,
    executable: str,
    profile: str,
    backend: str,
    result: str,
    pid: int | None = None,
    refused_reason: str = "",
    override_reason: str = "",
    base_dir: str | None = None,
) -> None:
    """Append one launch event.  Never raises into the caller's flow."""
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "app_id": app_id,
        "executable": os.path.basename(executable or ""),
        "profile": profile,
        "backend": backend,
        "result": result,  # "launched" | "refused" | "override" | "copied-in"
        "pid": pid,
        "refused_reason": refused_reason,
        "override_reason": override_reason,
    }
    try:
        line = json.dumps(entry, separators=(",", ":"))
    except (TypeError, ValueError):
        return
    try:
        path = _log_path(base_dir)
        d = os.path.dirname(path)
        os.makedirs(d, exist_ok=True)
        with _log_lock:
            _rotate(path)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            try:
                os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
    except OSError:
        logger = __import__("logging").getLogger(__name__)
        logger.debug("sandbox audit write failed", exc_info=True)


def _rotate(path: str) -> None:
    try:
        if os.path.getsize(path) <= _MAX_BYTES:
            return
    except OSError:
        return
    rotated = path + ".1"
    try:
        if os.path.exists(rotated):
            os.remove(rotated)
        os.replace(path, rotated)
    except OSError:
        pass


def read_launches(limit: int = 200, base_dir: str | None = None) -> list[dict]:
    """Most recent audit entries (newest first).  Corrupt lines are skipped."""
    path = _log_path(base_dir)
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return out
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
        if len(out) >= limit:
            break
    return out


def audit_file_mode_ok(base_dir: str | None = None) -> bool:
    """True when the audit log (if present) is user-only 0600."""
    path = _log_path(base_dir)
    try:
        return stat.S_IMODE(os.stat(path).st_mode) == 0o600
    except OSError:
        return True  # no log yet — nothing to protect
