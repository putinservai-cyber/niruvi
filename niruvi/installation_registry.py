import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path

from niruvi.settings import get_data_dir


class InstallationRecord:
    def __init__(self, name: str, path: str, version: str = "",
                 install_date: str = "", install_type: str = "extract",
                 source_sha256: str = "", desktop_file: str = "",
                 desktop_shortcut: str = "", update_url: str = "",
                 architecture: str = "", display_name_override: str = "",
                 custom_icon_path: str = "", env_vars: dict | None = None,
                 run_args: str = "", auto_update: bool = False,
                 update_channel: str = "stable"):
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
        )


class InstallationRegistry:
    def __init__(self):
        self._records: dict[str, InstallationRecord] = {}
        self._load()

    def _registry_file(self):
        return os.path.join(get_data_dir(), "registry.json")

    def _load(self):
        rf = self._registry_file()
        if os.path.exists(rf):
            try:
                with open(rf) as f:
                    data = json.load(f)
                for item in data:
                    record = InstallationRecord.from_dict(item)
                    self._records[record.name] = record
            except (json.JSONDecodeError, OSError) as e:
                logging.warning("Corrupted installation registry: %s", e)

    def _save(self):
        data_dir = get_data_dir()
        os.makedirs(data_dir, exist_ok=True)
        data = [r.to_dict() for r in self._records.values()]
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", dir=data_dir, delete=False, suffix=".tmp"
            ) as f:
                json.dump(data, f, indent=2)
                tmp = f.name
            os.replace(tmp, self._registry_file())
        except OSError:
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def add(self, record: InstallationRecord):
        self._records[record.name] = record
        self._save()

    def remove(self, name: str):
        self._records.pop(name, None)
        self._save()

    def get(self, name: str) -> InstallationRecord | None:
        return self._records.get(name)

    def get_all(self) -> list[InstallationRecord]:
        return list(self._records.values())

    def lookup_by_path(self, path: str) -> InstallationRecord | None:
        for record in self._records.values():
            if record.path == path:
                return record
        return None

    def lookup_by_name(self, name: str) -> InstallationRecord | None:
        return self._records.get(name)
