import json
import logging
import os
import tempfile
import threading
from datetime import datetime

from niruvi.config import get_data_dir
from niruvi.utils.integrity import read_json_with_hmac, write_json_with_hmac


class InstallationRecord:
    def __init__(
        self,
        name: str,
        path: str,
        version: str = "",
        install_date: str = "",
        install_type: str = "extract",
        source_sha256: str = "",
        desktop_file: str = "",
        desktop_shortcut: str = "",
        update_url: str = "",
        architecture: str = "",
        display_name_override: str = "",
        custom_icon_path: str = "",
        env_vars: dict | None = None,
        run_args: str = "",
        auto_update: bool = False,
        update_channel: str = "stable",
        sandbox_config: dict | None = None,
        size: int = 0,
    ):
        self.name = name
        self.path = path
        self.version = version
        self.install_date = install_date or datetime.now().isoformat()
        self.install_type = install_type
        self.source_sha256 = source_sha256
        self.desktop_file = desktop_file
        self.desktop_shortcut = desktop_shortcut
        self.update_url = update_url
        self.architecture = architecture
        self.display_name_override = display_name_override
        self.custom_icon_path = custom_icon_path
        self.env_vars = env_vars or {}
        self.run_args = run_args
        self.auto_update = auto_update
        self.update_channel = update_channel
        self.sandbox_config = sandbox_config or {}
        self.size = size

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "version": self.version,
            "install_date": self.install_date,
            "install_type": self.install_type,
            "source_sha256": self.source_sha256,
            "desktop_file": self.desktop_file,
            "desktop_shortcut": self.desktop_shortcut,
            "update_url": self.update_url,
            "architecture": self.architecture,
            "display_name_override": self.display_name_override,
            "custom_icon_path": self.custom_icon_path,
            "env_vars": self.env_vars,
            "run_args": self.run_args,
            "auto_update": self.auto_update,
            "update_channel": self.update_channel,
            "sandbox_config": self.sandbox_config,
            "size": self.size,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "InstallationRecord":
        return cls(
            name=data.get("name", ""),
            path=data.get("path", ""),
            version=data.get("version", ""),
            install_date=data.get("install_date", ""),
            install_type=data.get("install_type", "extract"),
            source_sha256=data.get("source_sha256", ""),
            desktop_file=data.get("desktop_file", ""),
            desktop_shortcut=data.get("desktop_shortcut", ""),
            update_url=data.get("update_url", ""),
            architecture=data.get("architecture", ""),
            display_name_override=data.get("display_name_override", ""),
            custom_icon_path=data.get("custom_icon_path", ""),
            env_vars=data.get("env_vars", {}),
            run_args=data.get("run_args", ""),
            auto_update=data.get("auto_update", False),
            update_channel=data.get("update_channel", "stable"),
            sandbox_config=data.get("sandbox_config", {}),
            size=data.get("size", 0),
        )


class InstallationRegistry:
    _instance: "InstallationRegistry | None" = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._records: dict[str, InstallationRecord] = {}
        self._path_index: dict[str, str] = {}
        self._loaded = False
        self._save_timer: threading.Timer | None = None
        self._save_pending = False
        self._save_lock = threading.Lock()

    def _ensure_loaded(self):
        if not self._loaded:
            self._load()
            self._loaded = True

    def _registry_file(self):
        return os.path.join(get_data_dir(), "registry.json")

    def _load(self):
        rf = self._registry_file()
        if not os.path.exists(rf):
            return
        data = None
        raw = read_json_with_hmac(rf)
        if raw is not None:
            data = raw
        if data is None:
            try:
                with open(rf) as f:
                    raw_json = json.load(f)
                if isinstance(raw_json, dict) and "_data" in raw_json:
                    raw_json = raw_json["_data"]
                data = raw_json
                logging.info("Loaded registry without HMAC (legacy format)")
            except (json.JSONDecodeError, OSError) as e:
                logging.warning("Corrupted installation registry: %s", e)
                return
        items = []
        if isinstance(data, dict):
            items = data.get("records", [])
            if not items:
                items = [v for v in data.values() if isinstance(v, dict) and "name" in v]
        elif isinstance(data, list):
            items = data
        for item in items:
            record = InstallationRecord.from_dict(item)
            self._records[record.name] = record
            if record.path:
                self._path_index[record.path] = record.name

    def _save(self):
        data_dir = get_data_dir()
        os.makedirs(data_dir, exist_ok=True)
        data = [r.to_dict() for r in self._records.values()]
        write_json_with_hmac(self._registry_file(), {"records": data})

    def _deferred_save(self):
        with self._save_lock:
            if self._save_pending:
                return
            self._save_pending = True
        if self._save_timer is not None:
            self._save_timer.cancel()
        self._save_timer = threading.Timer(0.5, self._flush_save)
        self._save_timer.daemon = True
        self._save_timer.start()

    def _flush_save(self):
        with self._save_lock:
            self._save_pending = False
        self._save()

    def add(self, record: InstallationRecord):
        self._ensure_loaded()
        self._records[record.name] = record
        if record.path:
            self._path_index[record.path] = record.name
        self._deferred_save()

    def remove(self, name: str):
        self._ensure_loaded()
        record = self._records.pop(name, None)
        if record and record.path:
            self._path_index.pop(record.path, None)
        self._deferred_save()

    def get(self, name: str) -> InstallationRecord | None:
        self._ensure_loaded()
        return self._records.get(name)

    def get_all(self) -> list[InstallationRecord]:
        self._ensure_loaded()
        return list(self._records.values())

    def lookup_by_path(self, path: str) -> InstallationRecord | None:
        self._ensure_loaded()
        name = self._path_index.get(path)
        if name:
            return self._records.get(name)
        for record in self._records.values():
            if record.path == path:
                self._path_index[path] = record.name
                return record
        return None

    def lookup_by_name(self, name: str) -> InstallationRecord | None:
        self._ensure_loaded()
        return self._records.get(name)

    def flush(self):
        """Force immediate save (for shutdown)."""
        self._flush_save()
