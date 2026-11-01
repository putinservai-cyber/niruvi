"""Per-application policy storage.

Policies live as one HMAC-signed JSON file per application under
``<data_dir>/policies/``.  Writes are atomic (temp file + ``os.replace`` via
:func:`write_json_with_hmac`), so an interrupted write can never corrupt an
existing policy.  A damaged file is preserved as ``<name>.invalid`` and the
application falls back to a sane default instead of losing the user's setup.

Each file also carries a small, append-only change history (Phase 13).
"""

import json
import logging
import os
import re
import time

from niruvi.config import get_data_dir
from niruvi.core.policy import POLICY_SCHEMA_VERSION, SandboxPolicy, preset_default
from niruvi.utils.integrity import read_json_with_hmac, write_json_with_hmac

logger = logging.getLogger(__name__)

MAX_HISTORY = 50


def _safe_id(app_id: str) -> str:
    """Map an arbitrary application id to a safe filename stem."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", app_id or "").strip("._-")
    if not stem:
        stem = "app"
    # Keep it bounded and unique-ish without hashing collisions.
    if len(stem) > 120:
        import hashlib

        stem = stem[:100] + "-" + hashlib.sha256(app_id.encode()).hexdigest()[:16]
    return stem


def _now() -> float:
    return time.time()


class PolicyStore:
    def __init__(self, base_dir: str | None = None):
        self._base = base_dir or os.path.join(get_data_dir(), "policies")

    def dir(self) -> str:
        return self._base

    def _path_for(self, app_id: str) -> str:
        return os.path.join(self._base, _safe_id(app_id) + ".json")

    def _read_payload(self, app_id: str) -> dict | None:
        path = self._path_for(app_id)
        if not os.path.isfile(path):
            return None
        data = read_json_with_hmac(path)
        if data is None:
            self._quarantine(path)
            return None
        return data

    def _quarantine(self, path: str):
        try:
            os.replace(path, path + ".invalid")
            logger.warning("Quarantined unreadable policy file: %s", path)
        except OSError as e:
            logger.warning("Could not quarantine %s: %s", path, e)

    def exists(self, app_id: str) -> bool:
        return os.path.isfile(self._path_for(app_id))

    def load(self, app_id: str, default: SandboxPolicy | None = None) -> SandboxPolicy:
        """Load the stored policy, or a default when absent/invalid."""
        payload = self._read_payload(app_id)
        if not payload:
            return default if default is not None else preset_default(app_id)
        policy_data = payload.get("policy")
        if not isinstance(policy_data, dict):
            logger.warning("Policy file for %r has no policy object — using default", app_id)
            return default if default is not None else preset_default(app_id)
        try:
            policy = SandboxPolicy.from_dict(policy_data)
        except Exception as e:
            logger.warning("Invalid policy for %r (%s) — using default", app_id, e)
            return default if default is not None else preset_default(app_id)
        if not policy.app_id:
            policy.app_id = app_id
        return policy

    def load_if_valid(self, app_id: str) -> SandboxPolicy | None:
        if not self.exists(app_id):
            return None
        return self.load(app_id)

    def save(self, app_id: str, policy: SandboxPolicy, note: str = "", changes: list[str] | None = None) -> bool:
        """Atomically persist a policy (with history). Returns success."""
        try:
            clean = policy.normalized()
        except Exception as e:
            logger.warning("Refusing to save invalid policy for %r: %s", app_id, e)
            return False
        clean.app_id = app_id
        payload = self._read_payload(app_id) or {}
        history = payload.get("history")
        if not isinstance(history, list):
            history = []
        if changes or note:
            history.append(
                {
                    "timestamp": _now(),
                    "note": note,
                    "changes": list(changes or []),
                }
            )
            history = history[-MAX_HISTORY:]
        data = {
            "app_id": app_id,
            "schema_version": POLICY_SCHEMA_VERSION,
            "updated_at": _now(),
            "policy": clean.to_dict(),
            "history": history,
        }
        try:
            os.makedirs(self._base, exist_ok=True)
            write_json_with_hmac(self._path_for(app_id), data)
            return True
        except OSError as e:
            logger.warning("Could not write policy for %r: %s", app_id, e)
            return False

    def delete(self, app_id: str) -> bool:
        path = self._path_for(app_id)
        try:
            if os.path.isfile(path):
                os.remove(path)
            return True
        except OSError as e:
            logger.warning("Could not delete policy for %r: %s", app_id, e)
            return False

    def history(self, app_id: str) -> list[dict]:
        payload = self._read_payload(app_id) or {}
        history = payload.get("history")
        return list(history) if isinstance(history, list) else []

    def list_ids(self) -> list[str]:
        if not os.path.isdir(self._base):
            return []
        ids = []
        for name in os.listdir(self._base):
            if name.endswith(".json") and not name.endswith(".invalid"):
                ids.append(name[:-5])
        return sorted(ids)

    # ── export / import (Phase 13) ───────────────────────────────────
    def export(self, app_id: str) -> dict | None:
        payload = self._read_payload(app_id)
        if payload is None:
            policy = self.load(app_id)
            payload = {
                "app_id": app_id,
                "schema_version": POLICY_SCHEMA_VERSION,
                "policy": policy.to_dict(),
                "history": [],
            }
        # Portable payload without the HMAC wrapper.
        return {
            "niruvi_policy": True,
            "exported_at": _now(),
            "app_id": payload.get("app_id", app_id),
            "schema_version": payload.get("schema_version", POLICY_SCHEMA_VERSION),
            "policy": payload.get("policy", {}),
            "history": payload.get("history", []),
        }

    def export_to_file(self, app_id: str, path: str) -> bool:
        data = self.export(app_id)
        if data is None:
            return False
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
            return True
        except OSError as e:
            logger.warning("Could not export policy to %s: %s", path, e)
            return False

    def _parse_import(self, data: dict) -> tuple[str, SandboxPolicy]:
        if not isinstance(data, dict) or not data.get("niruvi_policy"):
            raise ValueError("Not a Niruvi policy export")
        policy_data = data.get("policy")
        if not isinstance(policy_data, dict):
            raise ValueError("Export has no policy object")
        policy = SandboxPolicy.from_dict(policy_data)
        app_id = data.get("app_id") or policy.app_id
        if not app_id:
            raise ValueError("Export has no application id")
        # Importing must not silently widen access: validate before accepting.
        issues = policy.validate()
        if issues:
            raise ValueError("Imported policy is invalid: " + "; ".join(issues))
        policy.app_id = app_id
        return app_id, policy

    def import_data(self, data: dict, app_id: str | None = None, save: bool = True) -> SandboxPolicy:
        src_id, policy = self._parse_import(data)
        target = app_id or src_id
        policy.app_id = target
        if save:
            self.save(target, policy, note="Imported policy")
        return policy

    def import_from_file(self, path: str, app_id: str | None = None, save: bool = True) -> SandboxPolicy:
        with open(path) as f:
            data = json.load(f)
        return self.import_data(data, app_id=app_id, save=save)

    # ── external-modification detection (Phase 13) ───────────────────
    def mirror_is_stale(self, app_id: str, record) -> bool:
        """True when the registry's ``sandbox_config`` no longer matches the
        stored policy — i.e. something other than the permission UI changed it."""
        if record is None:
            return False
        from niruvi.core.policy import POLICY_SCHEMA_VERSION  # noqa: F401

        stored = self.load_if_valid(app_id)
        if stored is None:
            return False
        mirror = _mirror_from_policy(stored)
        current = _comparable(record.sandbox_config or {})
        return current != _comparable(mirror)


def resolve_policy(app_id: str, record=None, store: "PolicyStore | None" = None) -> SandboxPolicy:
    """Return the effective policy for an app.

    The policy file is authoritative. When it is absent (app installed by an
    older Niruvi), fall back to the registry's ``sandbox_config`` mirror so that
    existing behaviour — including "no sandbox configured" — is preserved.
    """
    store = store or PolicyStore()
    policy = store.load_if_valid(app_id)
    if policy is not None:
        if not policy.app_id:
            policy.app_id = app_id
        return policy
    if record is not None:
        cfg = dict(record.sandbox_config or {})
        if "enabled" not in cfg:
            cfg["enabled"] = False
        policy = SandboxPolicy.from_dict(cfg)
        policy.app_id = app_id
        return policy
    return preset_default(app_id)


def _comparable(d: dict) -> dict:
    """Reduce a config dict to the fields the legacy mirror understands."""
    keys = (
        "enabled",
        "backend",
        "portable_home",
        "portable_config",
        "enable_network",
        "hardening",
        "seccomp",
        "landlock",
        "use_namespace",
        "private_tmp",
        "memory_limit",
        "cpu_limit",
        "timeout",
    )
    out = {k: d.get(k) for k in keys if k in d}
    return out


def _mirror_from_policy(policy: SandboxPolicy) -> dict:
    """Compact config mirrored into the installation registry.

    Keeps the legacy launch/Isolation-tab fields in sync without embedding the
    full policy (which is authoritative in this store).
    """
    return {
        "enabled": policy.enabled,
        "backend": policy.backend,
        "portable_home": policy.portable_home,
        "portable_config": policy.portable_config,
        "enable_network": policy.network,
        "hardening": policy.hardening,
        "seccomp": policy.seccomp,
        "landlock": policy.landlock,
        "use_namespace": policy.use_namespace,
        "private_tmp": policy.private_tmp,
        "memory_limit": policy.memory_limit,
        "cpu_limit": policy.cpu_limit,
        "timeout": policy.timeout,
        # Paths are stored so backends/legacy code can still see them.
        "fs_readonly": [p for p, m in policy.path_to_grants() if m == "ro"],
        "fs_readwrite": [p for p, m in policy.path_to_grants() if m == "rw"],
        "fs_denied": policy.denied_paths(),
    }


def policy_mirror(policy: SandboxPolicy) -> dict:
    """Public accessor for the registry compatibility mirror."""
    return _mirror_from_policy(policy)
