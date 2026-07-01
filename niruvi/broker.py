"""Niruvi Permission Broker — capability-based permission system.

Pre-authorization store + runtime FIFO daemon for sandboxed apps
to request additional permissions during execution.

Architecture:
  - Permission registry: JSON file mapping app names to granted permissions
  - Runtime daemon: FIFO-based request/response broker for in-session requests
  - Permission categories: network, filesystem_read, filesystem_write, devices, camera, mic

Usage:
    from niruvi.broker import PermissionStore, PermissionDaemon

    # Check/store permissions
    broker = PermissionStore()
    broker.grant("myapp", "network")
    if broker.check("myapp", "network"):
        ...

    # Runtime daemon for in-session requests
    daemon = PermissionDaemon()
    daemon.start()
    fifo_dir = daemon.fifo_dir
    # App writes to fifo_dir/request -> daemon processes -> writes response
"""

import json
import logging
import os
import subprocess
import threading
import tempfile

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
    """Persistent permission storage per app."""

    def __init__(self):
        self._path = os.path.join(
            os.path.expanduser("~/.config/niruvi"), "permissions.json"
        )
        self._data: dict[str, dict[str, bool]] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        if os.path.exists(self._path):
            try:
                with open(self._path) as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def _save(self):
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        tmp = self._path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def grant(self, app_name: str, category: str, remember: bool = True):
        with self._lock:
            if app_name not in self._data:
                self._data[app_name] = {}
            self._data[app_name][category] = True
            if remember:
                self._save()

    def revoke(self, app_name: str, category: str):
        with self._lock:
            if app_name in self._data and category in self._data[app_name]:
                del self._data[app_name][category]
                self._save()

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

    def clear(self, app_name: str):
        with self._lock:
            self._data.pop(app_name, None)
            self._save()


class PermissionDaemon:
    """FIFO-based runtime permission broker.

    Sandboxed apps can request permissions by writing JSON to the FIFO.
    The daemon checks the permission store and responds via a response FIFO.

    Request format:  {"category": "network", "response_fifo": "/path/to/response"}
    Response format: {"category": "network", "granted": true}
    """

    def __init__(self):
        self._tmpdir: tempfile.TemporaryDirectory | None = None
        self._fifo_path: str | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._store = PermissionStore()
        self.fifo_dir: str | None = None

    def start(self) -> str | None:
        try:
            self._tmpdir = tempfile.TemporaryDirectory(prefix="niruvi-perm-broker-")
            self.fifo_dir = self._tmpdir.name
            self._fifo_path = os.path.join(self.fifo_dir, "request")
            os.mkfifo(self._fifo_path, 0o600)
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
                with open(self._fifo_path, "r") as fifo:
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
            if not category or not response_fifo:
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
