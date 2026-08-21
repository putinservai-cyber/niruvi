"""SQLite-backed installation registry.

Tracks installed AppImages in ``registry.db`` (SQLite) with metadata such as
name, version, install date, source URL, sandbox config and tags. Each row is
HMAC-signed so tampering is detected on load (same key as the JSON settings
file). A legacy ``registry.json`` file is automatically migrated on first run.

The public API (``add``/``remove``/``get``/``get_all``/``lookup_*``/``flush``)
is unchanged from the previous JSON implementation, so callers do not need to
know the storage backend.
"""

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime

from niruvi.config import get_data_dir
from niruvi.utils.integrity import read_json_with_hmac, sign_data, verify_data

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS installed_apps (
    name TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    version TEXT NOT NULL DEFAULT '',
    install_date TEXT NOT NULL DEFAULT '',
    install_type TEXT NOT NULL DEFAULT 'extract',
    source_sha256 TEXT NOT NULL DEFAULT '',
    desktop_file TEXT NOT NULL DEFAULT '',
    desktop_shortcut TEXT NOT NULL DEFAULT '',
    update_url TEXT NOT NULL DEFAULT '',
    architecture TEXT NOT NULL DEFAULT '',
    display_name_override TEXT NOT NULL DEFAULT '',
    custom_icon_path TEXT NOT NULL DEFAULT '',
    env_vars_json TEXT NOT NULL DEFAULT '{}',
    run_args TEXT NOT NULL DEFAULT '',
    auto_update INTEGER NOT NULL DEFAULT 0,
    update_channel TEXT NOT NULL DEFAULT 'stable',
    sandbox_config_json TEXT NOT NULL DEFAULT '{}',
    size INTEGER NOT NULL DEFAULT 0,
    tags_json TEXT NOT NULL DEFAULT '[]',
    hmac TEXT NOT NULL DEFAULT ''
)
"""

_COLUMNS = [
    "name",
    "path",
    "version",
    "install_date",
    "install_type",
    "source_sha256",
    "desktop_file",
    "desktop_shortcut",
    "update_url",
    "architecture",
    "display_name_override",
    "custom_icon_path",
    "env_vars_json",
    "run_args",
    "auto_update",
    "update_channel",
    "sandbox_config_json",
    "size",
    "tags_json",
    "hmac",
]


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
        tags: list[str] | None = None,
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
        self.tags = tags or []

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
            "tags": list(self.tags),
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
            tags=data.get("tags") or [],
        )


class InstallationRegistry:
    _instance: "InstallationRegistry | None" = None
    _instance_lock = threading.Lock()
    _initialized: bool

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

    def _db_path(self):
        return os.path.join(get_data_dir(), "registry.db")

    def _legacy_json_path(self):
        return os.path.join(get_data_dir(), "registry.json")

    def _connect(self) -> sqlite3.Connection:
        db = self._db_path()
        os.makedirs(os.path.dirname(db), exist_ok=True)
        conn = sqlite3.connect(db, timeout=10)
        conn.row_factory = sqlite3.Row
        # Wait up to 10s for a lock to clear instead of raising immediately; this
        # lets a second process (e.g. the post-update ghost) retry rather than fail.
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute(_SCHEMA)
        return conn

    def _load(self):
        db = self._db_path()
        if os.path.exists(db):
            self._load_db()
            return
        legacy = self._legacy_json_path()
        if os.path.exists(legacy):
            self._migrate_from_json(legacy)

    def _load_db(self):
        conn = None
        try:
            conn = self._connect()
            rows = conn.execute("SELECT * FROM installed_apps").fetchall()
        except sqlite3.Error as e:
            logger.warning("Could not read SQLite registry: %s", e)
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
            return
        finally:
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
        for row in rows:
            record_dict = _row_to_record_dict(row)
            stored_hmac = row["hmac"] or ""
            if stored_hmac and not verify_data(record_dict, stored_hmac):
                logger.warning("Registry entry %r failed HMAC check — skipping", record_dict.get("name"))
                continue
            record = InstallationRecord.from_dict(record_dict)
            self._records[record.name] = record
            if record.path:
                self._path_index[record.path] = record.name

    def _migrate_from_json(self, legacy_path: str):
        """Import records from a legacy (HMAC-protected) registry.json file."""
        raw = read_json_with_hmac(legacy_path)
        items = []
        if isinstance(raw, dict):
            items = raw.get("records", [])
            if not items:
                items = [v for v in raw.values() if isinstance(v, dict) and "name" in v]
        elif isinstance(raw, list):
            items = raw
        imported = 0
        for item in items:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            record = InstallationRecord.from_dict(item)
            self._records[record.name] = record
            if record.path:
                self._path_index[record.path] = record.name
            imported += 1
        if imported:
            self._save()
            logger.info("Migrated %d record(s) from %s to SQLite registry", imported, legacy_path)
        else:
            logger.debug("No records to migrate from %s", legacy_path)

    def _save(self, target: str | None = None):
        if target is not None:
            # Keep the deferred-save plumbing simple: only the DB path is used now.
            target = None
        db = self._db_path()
        os.makedirs(os.path.dirname(db), exist_ok=True)
        col_list = ", ".join(_COLUMNS)
        placeholders = ", ".join("?" for _ in _COLUMNS)
        update_cols = ", ".join(f"{c} = excluded.{c}" for c in _COLUMNS if c != "name")
        upsert_sql = (
            f"INSERT INTO installed_apps ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT(name) DO UPDATE SET {update_cols}"
        )
        for attempt in range(3):
            conn = sqlite3.connect(db, timeout=10)
            try:
                conn.execute(_SCHEMA)
                with conn:
                    for record in self._records.values():
                        data = record.to_dict()
                        conn.execute(upsert_sql, _record_to_row(data))
                    names = list(self._records.keys())
                    if names:
                        qmarks = ", ".join("?" for _ in names)
                        conn.execute(f"DELETE FROM installed_apps WHERE name NOT IN ({qmarks})", names)
                    else:
                        conn.execute("DELETE FROM installed_apps")
                break
            except sqlite3.OperationalError as e:
                logger.warning("Registry write retried (attempt %d): %s", attempt + 1, e)
                if attempt == 2:
                    logger.warning("Could not write SQLite registry after retries: %s", e)
            finally:
                conn.close()
        try:
            os.chmod(db, 0o600)
        except OSError:
            pass

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

    def reset(self):
        """Clear all in-memory state and pending saves (for tests).

        The on-disk database is left intact so callers can re-trigger a load;
        tests use an isolated data dir via NIRUVI_DATA_DIR instead.
        """
        with self._save_lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
                self._save_timer = None
            self._save_pending = False
        self._records.clear()
        self._path_index.clear()
        self._loaded = False


def _record_to_row(data: dict) -> tuple:
    return (
        data["name"],
        data["path"],
        data.get("version", ""),
        data.get("install_date", ""),
        data.get("install_type", "extract"),
        data.get("source_sha256", ""),
        data.get("desktop_file", ""),
        data.get("desktop_shortcut", ""),
        data.get("update_url", ""),
        data.get("architecture", ""),
        data.get("display_name_override", ""),
        data.get("custom_icon_path", ""),
        json.dumps(data.get("env_vars", {}), sort_keys=True),
        data.get("run_args", ""),
        1 if data.get("auto_update", False) else 0,
        data.get("update_channel", "stable"),
        json.dumps(data.get("sandbox_config", {}), sort_keys=True),
        int(data.get("size", 0)),
        json.dumps(data.get("tags", []), sort_keys=True),
        sign_data(data),
    )


def _row_to_record_dict(row: sqlite3.Row) -> dict:
    return {
        "name": row["name"],
        "path": row["path"],
        "version": row["version"],
        "install_date": row["install_date"],
        "install_type": row["install_type"],
        "source_sha256": row["source_sha256"],
        "desktop_file": row["desktop_file"],
        "desktop_shortcut": row["desktop_shortcut"],
        "update_url": row["update_url"],
        "architecture": row["architecture"],
        "display_name_override": row["display_name_override"],
        "custom_icon_path": row["custom_icon_path"],
        "env_vars": json.loads(row["env_vars_json"] or "{}"),
        "run_args": row["run_args"],
        "auto_update": bool(row["auto_update"]),
        "update_channel": row["update_channel"],
        "sandbox_config": json.loads(row["sandbox_config_json"] or "{}"),
        "size": row["size"],
        "tags": json.loads(row["tags_json"] or "[]"),
    }
