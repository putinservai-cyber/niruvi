"""Niruvi Permission Broker — capability-based permission system.

Pre-authorization store + runtime FIFO daemon for sandboxed apps
to request additional permissions during execution.

Architecture:
  - Permission registry: JSON file mapping app names to granted permissions
  - Runtime daemon: FIFO-based request/response broker with auth token
  - Permission categories: network, filesystem_read, filesystem_write, devices, camera, mic
"""

import json
import logging
import os
import secrets
import tempfile
import threading
import time

from niruvi.utils.integrity import read_json_with_hmac, write_json_with_hmac

logger = logging.getLogger(__name__)

PERMISSION_CATEGORIES = {
    "network": "Allow network access",
    "filesystem_read": "Read files outside sandbox",
    "filesystem_write": "Write files outside sandbox",
    "devices": "Access hardware devices (camera, mic)",
    "notifications": "Show desktop notifications",
    "usb": "Access USB devices",
    "bluetooth": "Access Bluetooth",
    "location": "Access location data",
}


class PermissionStore:
    """Persistent permission storage per app with audit logging."""

    def __init__(self):
        self._dir = os.path.join(os.path.expanduser("~/.config/niruvi"))
        self._path = os.path.join(self._dir, "permissions.json")
        self._audit_path = os.path.join(self._dir, "permissions.log")
        self._data: dict[str, dict[str, bool]] = {}
        self._lock = threading.Lock()
        self._load()

    def _ensure_perms(self):
        try:
            os.makedirs(self._dir, exist_ok=True)
            os.chmod(self._dir, 0o700)
        except OSError:
            pass

    def _load(self):
        if os.path.exists(self._path):
            loaded = read_json_with_hmac(self._path)
            if loaded is not None:
                self._data = loaded
                return
            try:
                with open(self._path) as f:
                    self._data = json.load(f)
                logger.info("Loaded permissions without HMAC (legacy format)")
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def _save(self):
        self._ensure_perms()
        write_json_with_hmac(self._path, self._data)

    def _audit(self, app_name: str, category: str, action: str):
        try:
            with open(self._audit_path, "a") as f:
                f.write(f"{time.time():.0f} | {action} | {app_name} | {category}\n")
            os.chmod(self._audit_path, 0o600)
        except OSError:
            pass

    def grant(self, app_name: str, category: str, remember: bool = True):
        with self._lock:
            if app_name not in self._data:
                self._data[app_name] = {}
            self._data[app_name][category] = True
            if remember:
                self._save()
            self._audit(app_name, category, "GRANT")

    def grant_transient(self, app_name: str, category: str):
        """Grant a permission that is not persisted (in-memory only)."""
        with self._lock:
            if app_name not in self._data:
                self._data[app_name] = {}
            self._data[app_name][category] = True
            self._audit(app_name, category, "GRANT_TRANSIENT")

    def revoke(self, app_name: str, category: str):
        with self._lock:
            if app_name in self._data and category in self._data[app_name]:
                del self._data[app_name][category]
                self._save()
                self._audit(app_name, category, "REVOKE")

    def check(self, app_name: str, category: str) -> bool:
        with self._lock:
            return self._data.get(app_name, {}).get(category, False)

    def is_granted(self, app_name: str, category: str) -> bool:
        return self.check(app_name, category)

    def get_all(self, app_name: str) -> dict[str, bool]:
        with self._lock:
            return dict(self._data.get(app_name, {}))

    def set_all(self, app_name: str, perms: dict[str, bool]):
        with self._lock:
            self._data[app_name] = dict(perms)
            self._save()
            for cat in perms:
                self._audit(app_name, cat, "SET" if perms[cat] else "CLEAR")

    def clear(self, app_name: str):
        with self._lock:
            if app_name in self._data:
                for cat in self._data[app_name]:
                    self._audit(app_name, cat, "REVOKE_ON_UNINSTALL")
                self._data.pop(app_name, None)
                self._save()

    def get_all_apps(self) -> list[str]:
        with self._lock:
            return list(self._data.keys())


class PermissionDaemon:
    """FIFO-based runtime permission broker with auth token.

    Sandboxed apps can request permissions by writing JSON to the FIFO.
    The daemon checks the permission store and responds via a response FIFO.

    Auth: a random token is written to a file in the FIFO directory.
    Clients must include this token in their requests.

    Request format:  {"category": "network", "response_fifo": "/path/to/response", "token": "..."}
    Response format: {"category": "network", "granted": true}
    """

    def __init__(self):
        self._tmpdir: tempfile.TemporaryDirectory | None = None
        self._fifo_path: str | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._store = PermissionStore()
        self.fifo_dir: str | None = None
        self._auth_token: str = ""

    def start(self) -> str | None:
        try:
            self._tmpdir = tempfile.TemporaryDirectory(prefix="niruvi-perm-broker-")
            self.fifo_dir = self._tmpdir.name
            os.chmod(self.fifo_dir, 0o700)
            self._fifo_path = os.path.join(self.fifo_dir, "request")
            os.mkfifo(self._fifo_path, 0o600)

            # Generate and store auth token
            self._auth_token = secrets.token_hex(32)
            token_path = os.path.join(self.fifo_dir, "token")
            with open(token_path, "w") as f:
                f.write(self._auth_token + "\n")
            os.chmod(token_path, 0o400)

            self._running = True
            self._thread = threading.Thread(target=self._listener, daemon=True)
            self._thread.start()
            return self.fifo_dir
        except Exception as e:
            logger.warning("PermissionDaemon start failed: %s", e)
            self._cleanup()
            return None

    def _listener(self):
        while self._running:
            try:
                with open(self._fifo_path) as fifo:
                    for line in fifo:
                        self._handle_request(line.strip())
            except OSError:
                break
            except Exception as e:
                logger.debug("Perm daemon error: %s", e)

    def _handle_request(self, data: str):
        if not data:
            return
        try:
            req = json.loads(data)
            category = req.get("category", "")
            app_name = req.get("app_name", "")
            response_fifo = req.get("response_fifo", "")
            token = req.get("token", "")

            if token != self._auth_token:
                logger.warning("Permission request with invalid auth token")
                return

            if not category or not response_fifo:
                return

            if self._tmpdir:
                real_fifo = os.path.realpath(response_fifo)
                real_tmpdir = os.path.realpath(self._tmpdir.name)
                if not real_fifo.startswith(real_tmpdir + os.sep):
                    logger.warning("Blocked response_fifo outside tmpdir: %s", response_fifo)
                    return

            granted = self._store.check(app_name, category) if app_name else False
            response = json.dumps({"category": category, "granted": granted})

            try:
                with open(response_fifo, "w") as f:
                    f.write(response + "\n")
            except OSError:
                pass
        except json.JSONDecodeError:
            pass
        except Exception as e:
            logger.debug("Perm request handling failed: %s", e)

    def stop(self):
        self._running = False
        self._cleanup()

    def _cleanup(self):
        if self._tmpdir:
            try:
                self._tmpdir.cleanup()
            except OSError:
                pass
            self._tmpdir = None
            self.fifo_dir = None


_permission_store: PermissionStore | None = None
_daemon: PermissionDaemon | None = None
_daemon_lock = threading.Lock()


def get_permission_store() -> PermissionStore:
    global _permission_store
    if _permission_store is None:
        _permission_store = PermissionStore()
    return _permission_store


def get_daemon() -> PermissionDaemon | None:
    global _daemon
    if _daemon is None:
        with _daemon_lock:
            if _daemon is None:
                d = PermissionDaemon()
                if d.start():
                    _daemon = d
                else:
                    return None
    return _daemon
