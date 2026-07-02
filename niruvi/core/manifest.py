"""Package Manifest — structured application metadata for Niruvi packages.

Every self-installing AppImage built by Niruvi embeds a .niruvi-manifest.json
in its .niruvi-install/ directory. The manifest describes the application,
its runtime, dependencies, permissions, update configuration, and installer
settings. Tools for creation, validation, and merging are provided here.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MANIFEST_VERSION = "1.0"
MANIFEST_FILENAME = ".niruvi-manifest.json"
MANIFEST_SCHEMA = "https://niruvi.app/schemas/manifest-v1.json"

_REQUIRED_KEYS = {"manifest_version", "app_id", "app_name", "version", "build"}
_VALID_INSTALL_TYPES = {"typical", "custom", "portable", "developer", "repair", "silent"}
_VALID_UPDATE_CHANNELS = {"stable", "beta", "nightly"}
_VALID_CATEGORIES = {
    "Development", "Utility", "Graphics", "Network", "Office",
    "AudioVideo", "Game", "Science", "System", "Education",
    "Settings", "Other",
}


class ManifestError(Exception):
    """Raised when manifest validation fails."""


class Manifest:
    """Structured package manifest with validation on construction."""

    def __init__(self, data: dict[str, Any] | None = None):
        self._data: dict[str, Any] = {
            "manifest_version": MANIFEST_VERSION,
            "schema": MANIFEST_SCHEMA,
            "app_id": "",
            "app_name": "",
            "version": "1.0.0",
            "publisher": "",
            "description": "",
            "category": "Utility",
            "build": {
                "builder": "Niruvi",
                "builder_version": "",
                "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "compression": "squashfs",
                "runtime": "",
            },
            "entry_point": {
                "executable": "AppRun",
                "working_dir": "",
                "env": {},
            },
            "dependencies": {
                "system": [],
                "runtime": "",
            },
            "permissions": {
                "network": False,
                "audio": False,
                "camera": False,
                "notifications": False,
                "removable_storage": False,
            },
            "update": {
                "url": "",
                "channel": "stable",
                "auto_update": False,
                "verify_signature": False,
            },
            "signing": {
                "algorithm": "",
                "fingerprint": "",
                "signature": "",
                "timestamp": "",
            },
            "installer": {
                "type": "typical",
                "silent": True,
                "rollback": True,
                "welcome_message": "",
                "finish_message": "",
                "launch_at_finish": True,
                "license_file": "",
            },
            "uninstaller": {
                "remove_config": False,
                "remove_user_data": False,
                "remove_logs": False,
            },
            "file_associations": [],
            "components": [],
            "custom_pages": [],
            "launch_count": 0,
            "install_date": "",
            "last_updated": "",
        }
        if data:
            self.merge(data)
        self.validate()

    def merge(self, data: dict[str, Any]) -> "Manifest":
        self._deep_merge(self._data, data)
        return self

    @staticmethod
    def _deep_merge(base: dict, overlay: dict):
        for key, val in overlay.items():
            if key in base and isinstance(base[key], dict) and isinstance(val, dict):
                Manifest._deep_merge(base[key], val)
            else:
                base[key] = val

    def validate(self):
        missing = _REQUIRED_KEYS - set(self._data.keys())
        if missing:
            raise ManifestError(f"Missing required keys: {missing}")
        itype = self._data.get("installer", {}).get("type", "")
        if itype and itype not in _VALID_INSTALL_TYPES:
            raise ManifestError(f"Invalid install type: {itype}")
        channel = self._data.get("update", {}).get("channel", "")
        if channel and channel not in _VALID_UPDATE_CHANNELS:
            raise ManifestError(f"Invalid update channel: {channel}")
        cat = self._data.get("category", "")
        if cat and cat not in _VALID_CATEGORIES:
            raise ManifestError(f"Invalid category: {cat}")

    @property
    def app_id(self) -> str:
        return self._data.get("app_id", "")

    @property
    def app_name(self) -> str:
        return self._data.get("app_name", "")

    @property
    def version(self) -> str:
        return self._data.get("version", "")

    @property
    def update_url(self) -> str:
        return self._data.get("update", {}).get("url", "")

    @property
    def installer_type(self) -> str:
        return self._data.get("installer", {}).get("type", "typical")

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self._data, indent=indent, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "Manifest":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ManifestError(f"Invalid JSON: {e}") from e
        return cls(data)

    @classmethod
    def from_file(cls, path: str | os.PathLike) -> "Manifest":
        return cls.from_json(Path(path).read_text(encoding="utf-8"))

    def to_file(self, path: str | os.PathLike):
        Path(path).write_text(self.to_json(), encoding="utf-8")

    def __eq__(self, other):
        if not isinstance(other, Manifest):
            return NotImplemented
        return self._eq_data(self._data) == self._eq_data(other._data)

    @staticmethod
    def _eq_data(d):
        c = dict(d)
        if "build" in c and "timestamp" in c["build"]:
            c["build"] = {k: v for k, v in c["build"].items() if k != "timestamp"}
        return c

    def __repr__(self):
        return f"<Manifest app_id={self.app_id!r} version={self.version!r}>"


def default_manifest(
    app_id: str, app_name: str, version: str = "1.0.0",
    publisher: str = "", description: str = "", category: str = "Utility",
    update_url: str = "", installer_type: str = "typical",
) -> Manifest:
    return Manifest({
        "app_id": app_id,
        "app_name": app_name,
        "version": version,
        "publisher": publisher,
        "description": description,
        "category": category,
        "update": {"url": update_url, "channel": "stable"},
        "installer": {"type": installer_type},
        "build": {"timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")},
    })


def find_manifest(appdir: str | os.PathLike) -> str | None:
    candidates = [
        Path(appdir) / ".niruvi-install" / MANIFEST_FILENAME,
        Path(appdir) / MANIFEST_FILENAME,
        Path(appdir) / ".niruvi-manifest.json",
    ]
    for p in candidates:
        if p.is_file():
            return str(p)
    return None


def load_manifest(appdir: str | os.PathLike) -> Manifest | None:
    path = find_manifest(appdir)
    if path:
        try:
            return Manifest.from_file(path)
        except (ManifestError, OSError, json.JSONDecodeError):
            return None
    return None
