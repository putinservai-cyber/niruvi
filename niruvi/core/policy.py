"""Application permission policy model for Niruvi.

An AppImage has no permission system of its own: it runs as the user, so
*access* is granted by the kernel to the process.  Any real restriction must be
imposed by a sandbox at launch time (Landlock/Firejail/Bubblewrap).  This module
defines the **policy** Niruvi stores per application and hands to a backend; the
backend is responsible for turning it into enforcement (see ``core.backends``).

The model is deliberately independent of any UI toolkit so it can be validated
and unit-tested without a display.  It is versioned: ``SandboxPolicy.from_dict``
migrates older payloads instead of discarding them.
"""

import logging
import os
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

POLICY_SCHEMA_VERSION = 2

FS_MODES = ("none", "ro", "rw", "deny")
_DIR_KEYS = ("downloads", "documents", "pictures", "videos", "music", "desktop")
_DIR_ENV = {
    "downloads": "XDG_DOWNLOAD_DIR",
    "documents": "XDG_DOCUMENTS_DIR",
    "pictures": "XDG_PICTURES_DIR",
    "videos": "XDG_VIDEOS_DIR",
    "music": "XDG_MUSIC_DIR",
    "desktop": "XDG_DESKTOP_DIR",
}
_DIR_DEFAULT = {
    "downloads": "~/Downloads",
    "documents": "~/Documents",
    "pictures": "~/Pictures",
    "videos": "~/Videos",
    "music": "~/Music",
    "desktop": "~/Desktop",
}

# Devices the backends know how to reason about.  Not all are enforceable.
DEVICE_KEYS = ("gpu", "audio", "microphone", "camera", "usb", "input")
IPC_KEYS = ("wayland", "x11", "dbus_session", "dbus_system", "ipc", "printing", "ssh_agent")

_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Variable names that would defeat the sandbox if set by the user.
_DANGEROUS_ENV = {
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "LD_AUDIT",
    "LD_DEBUG",
}


class PolicyError(ValueError):
    """Raised when a policy cannot be validated/normalised."""


def validate_path(raw: str) -> str:
    """Normalise and validate a user-supplied filesystem path.

    Returns an absolute, ``..``-free path.  The path need not exist yet (a
    parent that exists is enough to resolve symlinks safely).  Raises
    :class:`PolicyError` for empty, relative, or traversal paths.
    """
    if raw is None:
        raise PolicyError("Path is empty")
    path = str(raw).strip()
    if not path:
        raise PolicyError("Path is empty")
    if "\0" in path:
        raise PolicyError("Path contains a null byte")
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        raise PolicyError(f"Path must be absolute: {raw!r}")
    # Reject traversal before realpath so '..' cannot relabel a boundary.
    if ".." in path.split(os.sep):
        raise PolicyError(f"Path must not contain '..': {raw!r}")
    # Resolve symlinks on the longest existing prefix so a symlinked parent
    # cannot point outside the intended boundary, then re-attach the tail.
    parts = path.split(os.sep)
    resolved = os.sep
    for i, part in enumerate(parts):
        if part == "":
            continue
        candidate = os.path.join(resolved, part)
        if os.path.lexists(candidate):
            resolved = os.path.realpath(candidate)
        else:
            resolved = os.path.join(resolved, part)
        _ = i
    return resolved


def validate_env_name(name: str) -> str:
    name = (name or "").strip()
    if not _ENV_NAME_RE.match(name):
        raise PolicyError(f"Invalid environment variable name: {name!r}")
    return name


def validate_env_value(value: str) -> str:
    """Values are stored verbatim and passed via ``env`` (never a shell).

    Embedded NULs are rejected because they cannot be passed to ``execve``.
    """
    value = "" if value is None else str(value)
    if "\0" in value:
        raise PolicyError("Environment value contains a null byte")
    return value


@dataclass
class FsGrant:
    path: str
    mode: str = "rw"

    def to_dict(self) -> dict:
        return {"path": self.path, "mode": self.mode}

    @classmethod
    def from_dict(cls, d: dict) -> "FsGrant":
        return cls(path=d.get("path", ""), mode=d.get("mode", "rw"))


def _norm_mode(mode: str, default: str = "none") -> str:
    mode = (mode or default).lower()
    if mode not in FS_MODES:
        raise PolicyError(f"Unknown access mode: {mode!r}")
    return mode


@dataclass
class SandboxPolicy:
    """Per-application permission policy.

    Values describe *intent*; whether it can be enforced depends on the
    selected backend and kernel (see ``core.backends``).  A policy never claims
    enforcement by itself.
    """

    app_id: str = ""
    enabled: bool = True
    backend: str = "shield"

    # Filesystem
    home_access: str = "rw"
    dirs: dict = field(default_factory=lambda: dict.fromkeys(_DIR_KEYS, "rw"))
    removable: str = "none"
    custom_paths: list = field(default_factory=list)  # list[FsGrant]

    # Network / device / IPC
    network: bool = True
    devices: dict = field(default_factory=lambda: dict.fromkeys(DEVICE_KEYS, True))
    ipc: dict = field(default_factory=lambda: {k: True for k in IPC_KEYS})

    # Environment
    env: dict = field(default_factory=dict)

    # Resources
    memory_limit: str = ""
    cpu_limit: str = ""
    timeout: int = 0

    # Profile provenance (schema 2): which named profile produced this policy.
    # Older (schema 1) policies migrate to "custom" on load.
    profile: str = "custom"

    # Advanced
    hardening: bool = True
    seccomp: bool = True
    landlock: bool = True
    use_namespace: bool = True
    private_tmp: bool = False
    portable_home: bool = False
    portable_config: bool = False

    schema_version: int = POLICY_SCHEMA_VERSION

    # ── serialisation ────────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "schema_version": POLICY_SCHEMA_VERSION,
            "app_id": self.app_id,
            "enabled": self.enabled,
            "backend": self.backend,
            "profile": self.profile,
            "home_access": self.home_access,
            "dirs": dict(self.dirs),
            "removable": self.removable,
            "custom_paths": [g.to_dict() for g in self.custom_paths],
            "network": self.network,
            "devices": dict(self.devices),
            "ipc": dict(self.ipc),
            "env": dict(self.env),
            "memory_limit": self.memory_limit,
            "cpu_limit": self.cpu_limit,
            "timeout": self.timeout,
            "hardening": self.hardening,
            "seccomp": self.seccomp,
            "landlock": self.landlock,
            "use_namespace": self.use_namespace,
            "private_tmp": self.private_tmp,
            "portable_home": self.portable_home,
            "portable_config": self.portable_config,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "SandboxPolicy":
        data = dict(data or {})
        version = int(data.get("schema_version", 0) or 0)
        if version > POLICY_SCHEMA_VERSION:
            logger.warning(
                "Policy schema v%d is newer than supported v%d — reading best-effort",
                version,
                POLICY_SCHEMA_VERSION,
            )
        return cls._from_v1(data)

    @classmethod
    def _from_v1(cls, data: dict) -> "SandboxPolicy":
        dirs = dict.fromkeys(_DIR_KEYS, "none")
        # Schema 1 -> 2 migration: pre-profile policies carry no provenance.
        data.setdefault("profile", "custom")
        for key, value in (data.get("dirs") or {}).items():
            if key in dirs:
                dirs[key] = _norm_mode(value)
        devices = dict.fromkeys(DEVICE_KEYS, False)
        for key, value in (data.get("devices") or {}).items():
            if key in devices:
                devices[key] = bool(value)
        ipc = dict.fromkeys(IPC_KEYS, True)
        for key, value in (data.get("ipc") or {}).items():
            if key in ipc:
                ipc[key] = bool(value)
        custom = []
        for item in data.get("custom_paths") or []:
            if isinstance(item, dict) and item.get("path"):
                custom.append(FsGrant.from_dict(item))
            elif isinstance(item, str):
                custom.append(FsGrant(path=item, mode="rw"))
        policy = cls(
            app_id=data.get("app_id", ""),
            enabled=bool(data.get("enabled", True)),
            backend=data.get("backend", "shield"),
            profile=str(data.get("profile") or "custom").lower(),
            home_access=_norm_mode(data.get("home_access", "rw")),
            dirs=dirs,
            removable=_norm_mode(data.get("removable", "none"), "none"),
            custom_paths=custom,
            network=bool(data.get("network", True)),
            devices=devices,
            ipc=ipc,
            env={str(k): str(v) for k, v in (data.get("env") or {}).items()},
            memory_limit=str(data.get("memory_limit", "") or ""),
            cpu_limit=str(data.get("cpu_limit", "") or ""),
            timeout=int(data.get("timeout", 0) or 0),
            hardening=bool(data.get("hardening", True)),
            seccomp=bool(data.get("seccomp", True)),
            landlock=bool(data.get("landlock", True)),
            use_namespace=bool(data.get("use_namespace", True)),
            private_tmp=bool(data.get("private_tmp", False)),
            portable_home=bool(data.get("portable_home", False)),
            portable_config=bool(data.get("portable_config", False)),
        )
        return policy

    def copy(self) -> "SandboxPolicy":
        return SandboxPolicy.from_dict(self.to_dict())

    # ── validation / normalisation ───────────────────────────────────
    def validate(self) -> list[str]:
        """Return a list of human-readable problems (empty == valid)."""
        issues: list[str] = []
        if self.home_access not in FS_MODES:
            issues.append(f"Invalid home access: {self.home_access}")
        if self.removable not in FS_MODES:
            issues.append(f"Invalid removable access: {self.removable}")
        for key, mode in self.dirs.items():
            if mode not in FS_MODES:
                issues.append(f"Invalid access for {key}: {mode}")
        seen: set[str] = set()
        for grant in self.custom_paths:
            try:
                normalized = validate_path(grant.path)
            except PolicyError as e:
                issues.append(str(e))
                continue
            if grant.mode not in FS_MODES:
                issues.append(f"Invalid mode {grant.mode!r} for {grant.path}")
            if normalized == os.sep:
                issues.append("Granting access to '/' is not allowed")
            canonical = os.path.realpath(normalized)
            if canonical in seen:
                issues.append(f"Duplicate path: {grant.path}")
            seen.add(canonical)
        for name, value in self.env.items():
            try:
                validate_env_name(name)
                validate_env_value(value)
            except PolicyError as e:
                issues.append(str(e))
        if self.memory_limit and _parse_size(self.memory_limit) is None:
            issues.append(f"Unparseable memory limit: {self.memory_limit!r}")
        if self.cpu_limit:
            try:
                if float(self.cpu_limit) <= 0:
                    issues.append("CPU limit must be positive")
            except ValueError:
                issues.append(f"Unparseable CPU limit: {self.cpu_limit!r}")
        return issues

    def normalized(self) -> "SandboxPolicy":
        """Return a validated, normalised copy. Raises :class:`PolicyError`."""
        issues = self.validate()
        if issues:
            raise PolicyError("; ".join(issues))
        clean = self.copy()
        clean.env = {validate_env_name(k): validate_env_value(v) for k, v in self.env.items()}
        normalized: list[FsGrant] = []
        seen: set[str] = set()
        for grant in self.custom_paths:
            canonical = os.path.realpath(validate_path(grant.path))
            if canonical in seen:
                continue
            seen.add(canonical)
            normalized.append(FsGrant(path=canonical, mode=grant.mode))
        clean.custom_paths = normalized
        return clean

    # ── filesystem expansion ─────────────────────────────────────────
    def path_to_grants(self, home: str | None = None) -> list[tuple[str, str]]:
        """Expand the semantic filesystem fields into concrete ``(path, mode)``.

        Only existing directories are emitted (missing optional XDG dirs are
        skipped) so backends never fail on a path the user does not have.
        """
        home = home or os.path.expanduser("~")
        out: list[tuple[str, str]] = []
        if self.home_access != "none":
            out.append((home, self.home_access))
        for key, mode in self.dirs.items():
            if mode == "none":
                continue
            raw = os.environ.get(_DIR_ENV.get(key, "")) or os.path.expanduser(_DIR_DEFAULT[key])
            path = os.path.expanduser(raw)
            if os.path.isdir(path):
                out.append((path, mode))
        for grant in self.custom_paths:
            out.append((grant.path, grant.mode))
        return out

    def denied_paths(self) -> list[str]:
        return [g.path for g in self.custom_paths if g.mode == "deny"]

    def grants_access_ro(self) -> bool:
        return any(mode == "ro" for _p, mode in self.path_to_grants())

    def grants_access_rw(self) -> bool:
        return self.home_access == "rw" or any(mode == "rw" for _p, mode in self.path_to_grants())

    def uses_broad_home(self) -> bool:
        """True when the policy exposes the entire home directory."""
        return self.home_access in ("ro", "rw")

    def is_fully_restricted(self) -> bool:
        return (
            not self.network
            and self.home_access == "none"
            and all(m == "none" for m in self.dirs.values())
            and self.removable == "none"
            and not any(m == "rw" for _p, m in self.path_to_grants())
        )


def _parse_size(value: str) -> int | None:
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*([kmg]?)\s*$", str(value).strip().lower())
    if not m:
        return None
    mult = {"": 1, "k": 1024, "m": 1024 * 1024, "g": 1024 * 1024 * 1024}[m.group(2)]
    return int(float(m.group(1)) * mult)


# ── Presets ──────────────────────────────────────────────────────────


def _base_policy(app_id: str = "", backend: str = "shield") -> SandboxPolicy:
    return SandboxPolicy(app_id=app_id, backend=backend)


def preset_default(app_id: str = "", backend: str = "shield") -> SandboxPolicy:
    """Current Niruvi behaviour: sandbox on, home rw, standard dirs rw."""
    p = _base_policy(app_id, backend)
    p.home_access = "rw"
    p.dirs = dict.fromkeys(_DIR_KEYS, "rw")
    p.network = True
    p.devices = dict.fromkeys(DEVICE_KEYS, True)
    p.ipc = {k: True for k in IPC_KEYS}
    return p


def preset_restricted(app_id: str = "", backend: str = "shield") -> SandboxPolicy:
    p = _base_policy(app_id, backend)
    p.home_access = "none"
    p.dirs = dict.fromkeys(_DIR_KEYS, "none")
    p.removable = "none"
    p.network = False
    p.devices = dict.fromkeys(DEVICE_KEYS, False)
    p.ipc = {k: False for k in IPC_KEYS}
    p.ipc["wayland"] = True
    p.ipc["x11"] = True  # needs a display to show a window at all
    return p


def preset_development(app_id: str = "", backend: str = "shield") -> SandboxPolicy:
    p = preset_default(app_id, backend)
    p.dirs["documents"] = "rw"
    p.network = True
    p.ipc["ssh_agent"] = True
    return p


def preset_media_editing(app_id: str = "", backend: str = "shield") -> SandboxPolicy:
    p = preset_default(app_id, backend)
    for key in ("pictures", "videos", "music", "documents"):
        p.dirs[key] = "rw"
    p.devices["gpu"] = True
    p.devices["audio"] = True
    p.devices["camera"] = True
    p.network = False
    return p


def preset_gaming(app_id: str = "", backend: str = "shield") -> SandboxPolicy:
    p = preset_default(app_id, backend)
    p.network = True
    p.devices["gpu"] = True
    p.devices["audio"] = True
    p.devices["input"] = True
    p.ipc["dbus_session"] = True
    p.memory_limit = ""
    return p


PRESETS = {
    "default": ("Default (recommended)", preset_default),
    "restricted": ("Restricted", preset_restricted),
    "development": ("Development", preset_development),
    "media": ("Media editing", preset_media_editing),
    "gaming": ("Gaming", preset_gaming),
}


def preset_names() -> list[str]:
    return list(PRESETS.keys())


# ── Diff / preview ───────────────────────────────────────────────────

_MODE_LABEL = {"rw": "read/write", "ro": "read-only", "deny": "denied", "none": "no access"}


def describe_policy(policy: SandboxPolicy) -> list[str]:
    """Human-readable summary lines for a policy (used for previews)."""
    lines = []
    lines.append(f"Sandbox: {'enabled' if policy.enabled else 'disabled'}")
    lines.append(f"Network: {'allowed' if policy.network else 'blocked'}")
    lines.append(f"Home directory: {_MODE_LABEL.get(policy.home_access, policy.home_access)}")
    for key, mode in policy.dirs.items():
        if mode != "none":
            lines.append(f"  {key.capitalize()}: {_MODE_LABEL.get(mode, mode)}")
    if policy.removable != "none":
        lines.append(f"Removable storage: {_MODE_LABEL.get(policy.removable, policy.removable)}")
    for grant in policy.custom_paths:
        lines.append(f"  {grant.path}: {_MODE_LABEL.get(grant.mode, grant.mode)}")
    denied = [d for d in policy.devices if not policy.devices[d]]
    allowed_dev = [d for d in policy.devices if policy.devices[d]]
    if allowed_dev:
        lines.append("Devices: " + ", ".join(allowed_dev))
    for d in denied:
        lines.append(f"  {d}: blocked")
    env_keys = sorted(policy.env)
    if env_keys:
        lines.append("Environment: " + ", ".join(env_keys))
    return lines


def diff_policies(current: SandboxPolicy, proposed: SandboxPolicy) -> dict:
    """Compute a change summary between two policies.

    Returns ``{"changes": [str], "grants": bool, "revokes": bool}`` where
    ``grants`` is True if the proposal widens access anywhere.
    """
    changes: list[str] = []
    grants = False
    revokes = False

    def track(label, old, new, wider_values):
        nonlocal grants, revokes
        if old == new:
            return
        changes.append(f"{label}: {old} → {new}")
        if new in wider_values:
            grants = True
        if old in wider_values and new not in wider_values:
            revokes = True

    if current.enabled != proposed.enabled:
        changes.append(f"Sandbox enabled: {current.enabled} → {proposed.enabled}")
        if current.enabled and not proposed.enabled:
            grants = True
        if not current.enabled and proposed.enabled:
            revokes = True

    if current.network != proposed.network:
        changes.append(
            f"Network: {'allowed' if current.network else 'blocked'} → {'allowed' if proposed.network else 'blocked'}"
        )
        grants = grants or (proposed.network and not current.network)
        revokes = revokes or (current.network and not proposed.network)

    _mode_rank = {"none": 0, "deny": 0, "ro": 1, "rw": 2}
    track("Home directory", current.home_access, proposed.home_access, ("ro", "rw"))
    for key in _DIR_KEYS:
        old, new = current.dirs.get(key, "none"), proposed.dirs.get(key, "none")
        if old != new:
            changes.append(f"{key.capitalize()}: {_MODE_LABEL.get(old, old)} → {_MODE_LABEL.get(new, new)}")
        if _mode_rank.get(new, 0) > _mode_rank.get(old, 0):
            grants = True
        if _mode_rank.get(old, 0) > _mode_rank.get(new, 0):
            revokes = True
    if current.removable != proposed.removable:
        changes.append(
            f"Removable storage: {_MODE_LABEL.get(current.removable, current.removable)} → "
            f"{_MODE_LABEL.get(proposed.removable, proposed.removable)}"
        )
        if _mode_rank.get(proposed.removable, 0) > _mode_rank.get(current.removable, 0):
            grants = True
        if _mode_rank.get(current.removable, 0) > _mode_rank.get(proposed.removable, 0):
            revokes = True

    cur_paths = {g.path: g.mode for g in current.custom_paths}
    new_paths = {g.path: g.mode for g in proposed.custom_paths}
    for path in sorted(set(cur_paths) | set(new_paths)):
        old, new = cur_paths.get(path), new_paths.get(path)
        if old == new:
            continue
        if old is None:
            changes.append(f"Add path {path}: {_MODE_LABEL.get(new, new)}")
            if new in ("ro", "rw"):
                grants = True
        elif new is None:
            changes.append(f"Remove path {path}")
            revokes = True
        else:
            changes.append(f"{path}: {_MODE_LABEL.get(old, old)} → {_MODE_LABEL.get(new, new)}")
            if _mode_rank.get(new, 0) > _mode_rank.get(old, 0):
                grants = True
            if _mode_rank.get(old, 0) > _mode_rank.get(new, 0):
                revokes = True

    for key in DEVICE_KEYS:
        old, new = current.devices.get(key, False), proposed.devices.get(key, False)
        if old != new:
            changes.append(f"Device {key}: {'allowed' if old else 'blocked'} → {'allowed' if new else 'blocked'}")
            grants = grants or (new and not old)
            revokes = revokes or (old and not new)
    for key in IPC_KEYS:
        old, new = current.ipc.get(key, False), proposed.ipc.get(key, False)
        if old != new:
            changes.append(f"IPC {key}: {'allowed' if old else 'blocked'} → {'allowed' if new else 'blocked'}")
            grants = grants or (new and not old)
            revokes = revokes or (old and not new)

    cur_env, new_env = current.env, proposed.env
    for name in sorted(set(cur_env) | set(new_env)):
        if cur_env.get(name) != new_env.get(name):
            changes.append(f"Env {name}: {'set' if name in new_env else 'removed'}")

    for label, old, new in (
        ("Memory limit", current.memory_limit, proposed.memory_limit),
        ("CPU limit", current.cpu_limit, proposed.cpu_limit),
        ("Timeout", current.timeout, proposed.timeout),
        ("Private /tmp", current.private_tmp, proposed.private_tmp),
        ("Portable home", current.portable_home, proposed.portable_home),
        ("Portable config", current.portable_config, proposed.portable_config),
        ("Hardening", current.hardening, proposed.hardening),
        ("Seccomp", current.seccomp, proposed.seccomp),
        ("Landlock", current.landlock, proposed.landlock),
        ("User namespace", current.use_namespace, proposed.use_namespace),
    ):
        if old != new:
            changes.append(f"{label}: {old} → {new}")
            if label in ("Hardening", "Seccomp", "Landlock") and old and not new:
                grants = True

    return {"changes": changes, "grants": grants, "revokes": revokes}


def dangerous_changes(proposed: SandboxPolicy) -> list[str]:
    """Warn about settings that meaningfully widen the attack surface."""
    warnings: list[str] = []
    if not proposed.enabled:
        warnings.append("Sandboxing is disabled — the app will run with full user access.")
    if proposed.home_access == "rw":
        warnings.append("Read/write access to your entire home directory.")
    if "deny" in (g.mode for g in proposed.custom_paths):
        pass
    for name in proposed.env:
        if name in _DANGEROUS_ENV:
            warnings.append(f"{name} changes how libraries are loaded and can defeat the sandbox.")
    if proposed.memory_limit == "" and not proposed.cpu_limit and proposed.timeout == 0:
        pass
    return warnings
