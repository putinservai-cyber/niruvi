"""Sandbox backend abstraction for application permission policies.

A backend turns a :class:`~niruvi.core.policy.SandboxPolicy` into real
enforcement.  **Niruvi applies policy at launch time** for AppImages: it does
not (and cannot) rewrite an AppImage's own security model.  Each backend
therefore reports exactly which controls it can *really* enforce so the UI never
shows a switch that does nothing.

Capability levels (per control):
  ``supported``      — the backend enforces this reliably.
  ``partial``        — enforced with caveats (documented in ``detail``).
  ``unsupported``    — the backend cannot enforce this; the UI disables it.

Enforcement states:
  ``not_sandboxed``, ``sandboxed``, ``partially_restricted``,
  ``configuration_error``, ``backend_unavailable``, ``pending_changes``,
  ``enforcement_not_verified``.
"""

import functools
import logging
import subprocess
from dataclasses import dataclass, field

from niruvi.core.policy import SandboxPolicy
from niruvi.core.policy_store import policy_mirror

logger = logging.getLogger(__name__)


# ── capability descriptors ───────────────────────────────────────────

SUPPORTED = "supported"
PARTIAL = "partial"
UNSUPPORTED = "unsupported"


@dataclass
class Control:
    level: str
    detail: str = ""


# Enforcement states
NOT_SANDBOXED = "not_sandboxed"
SANDBOXED = "sandboxed"
PARTIALLY_RESTRICTED = "partially_restricted"
CONFIG_ERROR = "configuration_error"
BACKEND_UNAVAILABLE = "backend_unavailable"
PENDING_CHANGES = "pending_changes"
NOT_VERIFIED = "enforcement_not_verified"

STATE_LABELS = {
    NOT_SANDBOXED: "Not sandboxed",
    SANDBOXED: "Sandboxed",
    PARTIALLY_RESTRICTED: "Partially restricted",
    CONFIG_ERROR: "Configuration error",
    BACKEND_UNAVAILABLE: "Backend unavailable",
    PENDING_CHANGES: "Permission changes pending",
    NOT_VERIFIED: "Enforcement not verified",
}

STATE_DESCRIPTIONS = {
    NOT_SANDBOXED: "The application runs without any Niruvi sandbox.",
    SANDBOXED: "The selected backend reports that its restrictions are active.",
    PARTIALLY_RESTRICTED: "Some restrictions are enforced, but at least one requested control is not.",
    CONFIG_ERROR: "The stored policy is invalid and could not be applied.",
    BACKEND_UNAVAILABLE: "The selected sandbox backend is not installed on this system.",
    PENDING_CHANGES: "Edits have not been applied yet.",
    NOT_VERIFIED: "The backend could not confirm that restrictions took effect.",
}


@dataclass
class EnforcementStatus:
    state: str
    label: str = ""
    detail: str = ""
    backend: str = ""
    enforced: list = field(default_factory=list)
    not_enforced: list = field(default_factory=list)

    def __post_init__(self):
        if not self.label:
            self.label = STATE_LABELS.get(self.state, self.state)
        if not self.detail:
            self.detail = STATE_DESCRIPTIONS.get(self.state, "")


@dataclass
class ApplyResult:
    ok: bool
    error: str = ""
    warnings: list = field(default_factory=list)


@functools.lru_cache(maxsize=8)
def _probe_backend(name: str) -> dict:
    """Detection results, cached for the process lifetime.

    Detection shells out (``firejail --version`` / ``bwrap --version``) and
    walks the kernel Landlock/userns probes; the sidebar enumerates every app,
    so this must not run per application. Call :func:`clear_backend_cache` to
    re-detect (e.g. after the user installs a backend).
    """
    if name == "shield":
        from niruvi.core.sandbox import check_shield_available

        info = check_shield_available()
        info["available"] = True
        return info
    if name == "firejail":
        from niruvi.core.sandbox import check_firejail_available

        return check_firejail_available()
    if name == "bwrap":
        from niruvi.core.sandbox import check_bwrap_available

        return check_bwrap_available()
    return {"available": False, "error": "unknown backend"}


@functools.lru_cache(maxsize=8)
def _caps_cached(name: str) -> tuple:
    return tuple(_compute_capabilities(name).items())


def _compute_capabilities(name: str) -> dict:
    if name == "shield":
        from niruvi.core.sandbox import landlock_supported

        ll = landlock_supported()
        ns = _userns_available()
        return {
            "filesystem": Control(
                SUPPORTED if ll else PARTIAL,
                "Enforced with Landlock (kernel 5.13+)."
                if ll
                else "Landlock unavailable on this kernel; filesystem limits are not enforced.",
            ),
            "network": Control(
                SUPPORTED if ns else UNSUPPORTED,
                "Blocked by an isolated network namespace (unshare --net)."
                if ns
                else "Network blocking needs unprivileged user namespaces, which are unavailable.",
            ),
            "devices": Control(
                UNSUPPORTED,
                "Niruvi Shield cannot filter individual device nodes; use Bubblewrap or Firejail.",
            ),
            "ipc": Control(
                PARTIAL,
                "Display/D-Bus access is filtered through the environment; not a hard boundary.",
            ),
            "environment": Control(SUPPORTED, "Passed directly to the process environment."),
            "resources": Control(SUPPORTED, "Enforced with rlimits (memory, CPU) by the kernel."),
        }
    avail = bool(_probe_backend(name).get("available"))
    level = SUPPORTED if avail else UNSUPPORTED
    note = "" if avail else f"{name} is not installed."
    if name == "firejail":
        return {
            "filesystem": Control(level, note or "Enforced with Firejail whitelist/blacklist profiles."),
            "network": Control(level, note or "Enforced with --net=none."),
            "devices": Control(
                PARTIAL if avail else UNSUPPORTED,
                note
                or "GPU (--nodri), audio (--nosound), camera (--novideo) and touch "
                "input (--notouch) are blocked by flags; generic USB nodes stay reachable.",
            ),
            "ipc": Control(
                PARTIAL if avail else UNSUPPORTED,
                note or "X11/D-Bus/printing can be filtered; not all sockets are covered.",
            ),
            "environment": Control(level, note or "Passed directly to the process environment."),
            "resources": Control(PARTIAL if avail else UNSUPPORTED, note or "Enforced with --rlimit-*."),
        }
    return {
        "filesystem": Control(level, note or "Enforced with bind mounts."),
        "network": Control(level, note or "Enforced with --unshare-net."),
        "devices": Control(
            SUPPORTED if avail else UNSUPPORTED,
            note
            or "Each category is enforced by omitting its device nodes from the "
            "sandbox /dev — disallowed nodes are genuinely absent. "
            "Camera and microphone share nodes with video/audio hardware.",
        ),
        "ipc": Control(
            PARTIAL if avail else UNSUPPORTED,
            note
            or "Wayland/X11/D-Bus sockets are bound only when their policy allows it; "
            "application-supplied paths cannot be fully excluded.",
        ),
        "environment": Control(level, note or "Passed directly to the process environment."),
        "resources": Control(UNSUPPORTED, "Bubblewrap does not apply rlimits."),
    }


def clear_backend_cache():
    _probe_backend.cache_clear()
    _caps_cached.cache_clear()


class PermissionBackend:
    """Interface every backend implements."""

    name = "base"
    display_name = "Base"

    # ── detection ────────────────────────────────────────────────────
    def detect_support(self) -> dict:
        raise NotImplementedError

    def available(self) -> bool:
        return bool(self.detect_support().get("available"))

    def capabilities(self) -> dict:
        raise NotImplementedError

    def capability(self, control: str) -> Control:
        return self.capabilities().get(control, Control(UNSUPPORTED, "Unknown control"))

    # ── policy lifecycle ─────────────────────────────────────────────
    def inspect_policy(self, policy: SandboxPolicy, record=None) -> dict:
        return {
            "backend": self.name,
            "app_id": policy.app_id,
            "enabled": policy.enabled,
            "network": policy.network,
            "filesystem": policy.path_to_grants(),
            "denied": policy.denied_paths(),
        }

    def validate_policy(self, policy: SandboxPolicy) -> list[str]:
        issues = list(policy.validate())
        missing = [c for c, cap in self.capabilities().items() if cap.level == UNSUPPORTED]
        _ = missing  # unsupported controls are surfaced in the UI, not fatal
        return issues

    def preview_changes(self, current: SandboxPolicy, proposed: SandboxPolicy) -> dict:
        from niruvi.core.policy import diff_policies

        return diff_policies(current, proposed)

    def apply_policy(self, app_id: str, policy: SandboxPolicy, record=None, store=None) -> ApplyResult:
        """Persist policy + keep the registry mirror in sync."""
        raise NotImplementedError

    def launch_application(self, app_dir: str, cmd: list[str], env: dict | None, policy: SandboxPolicy):
        raise NotImplementedError

    def restore_previous_policy(self, app_id: str, previous: SandboxPolicy, record=None, store=None) -> bool:
        raise NotImplementedError

    def get_enforcement_status(self, policy: SandboxPolicy, pending: bool = False) -> EnforcementStatus:
        raise NotImplementedError

    # ── helpers ──────────────────────────────────────────────────────
    def _persist(self, app_id: str, policy: SandboxPolicy, record, store, note: str, changes: list) -> ApplyResult:
        from niruvi.core.policy_store import PolicyStore

        store = store or PolicyStore()
        saved = store.save(app_id, policy, note=note, changes=changes)
        if not saved:
            return ApplyResult(False, "Could not write the policy file.")
        if record is not None:
            try:
                record.sandbox_config = policy_mirror(policy)
                from niruvi.desktop.installation_registry import InstallationRegistry

                InstallationRegistry().add(record)
            except Exception as e:  # pragma: no cover - registry is best-effort
                logger.warning("Could not update registry mirror for %r: %s", app_id, e)
        return ApplyResult(True)


# ── Niruvi Shield (built-in) ─────────────────────────────────────────


class ShieldBackend(PermissionBackend):
    name = "shield"
    display_name = "Niruvi Shield"

    def detect_support(self) -> dict:
        return dict(_probe_backend("shield"))

    def capabilities(self) -> dict:
        return dict(_caps_cached("shield"))

    def validate_policy(self, policy: SandboxPolicy) -> list[str]:
        issues = super().validate_policy(policy)
        if policy.landlock and not _landlock_ok():
            issues.append("Landlock is requested but not available on this kernel.")
        if not policy.network and not policy.use_namespace:
            issues.append("Blocking network requires 'use user namespaces' to be enabled.")
        return issues

    def apply_policy(self, app_id, policy, record=None, store=None) -> ApplyResult:
        from niruvi.core.policy import diff_policies

        prev = None
        if record is not None:
            prev = SandboxPolicy.from_dict(record.sandbox_config or {})
        changes = diff_policies(prev, policy)["changes"] if prev else []
        result = self._persist(app_id, policy, record, store, note="Applied policy", changes=changes)
        if result.ok:
            result.warnings = self._enforcement_warnings(policy)
        return result

    def restore_previous_policy(self, app_id, previous, record=None, store=None) -> bool:
        result = self._persist(app_id, previous, record, store, note="Restored previous policy", changes=[])
        return result.ok

    def launch_application(self, app_dir, cmd, env, policy):
        from niruvi.core.sandbox import Shield, ShieldConfig

        config = ShieldConfig.from_policy(policy)
        return Shield(config).run(cmd, cwd=app_dir, env=env)

    def get_enforcement_status(self, policy, pending: bool = False) -> EnforcementStatus:
        if pending:
            return EnforcementStatus(PENDING_CHANGES, backend=self.name)
        if not policy.enabled:
            return EnforcementStatus(NOT_SANDBOXED, backend=self.name)
        issues = policy.validate()
        if issues:
            return EnforcementStatus(CONFIG_ERROR, detail="; ".join(issues), backend=self.name)
        enforced, not_enforced = [], []
        if policy.hardening:
            enforced.append("Process hardening (rlimits, ptrace, malloc)")
        if policy.landlock and _landlock_ok():
            enforced.append("Filesystem (Landlock)")
        elif policy.landlock:
            not_enforced.append("Filesystem (Landlock unavailable)")
        if policy.seccomp:
            enforced.append("Syscall filtering (seccomp)")
        if not policy.network:
            if policy.use_namespace and _userns_available():
                enforced.append("Network isolation")
            else:
                not_enforced.append("Network isolation (needs user namespaces)")
        if policy.portable_home:
            enforced.append("Portable home")
        if policy.portable_config:
            enforced.append("Portable config")
        not_enforced.extend(self._unverifiable_controls(policy))
        if not enforced:
            return EnforcementStatus(NOT_SANDBOXED, backend=self.name)
        if not_enforced:
            return EnforcementStatus(
                PARTIALLY_RESTRICTED,
                backend=self.name,
                enforced=enforced,
                not_enforced=not_enforced,
                detail="; ".join(not_enforced),
            )
        return EnforcementStatus(SANDBOXED, backend=self.name, enforced=enforced)

    def _unverifiable_controls(self, policy: SandboxPolicy) -> list[str]:
        out = []
        if not policy.is_fully_restricted() and policy.denied_paths():
            out.append("Denied paths inside a granted directory cannot be isolated by Landlock")
        denied_devices = [d for d in policy.devices if not policy.devices[d]]
        if denied_devices and self.name == "shield":
            out.append(
                "Device restrictions are placeholder entries only: Niruvi Shield cannot "
                "filter device nodes; use the Bubblewrap backend for real device isolation"
            )
        if not policy.ipc.get("wayland") or not policy.ipc.get("x11"):
            out.append("Display socket filtering is not enforced by Niruvi Shield")
        return out

    def _enforcement_warnings(self, policy: SandboxPolicy) -> list[str]:
        status = self.get_enforcement_status(policy)
        return [status.detail] if status.not_enforced else []


# ── Firejail ─────────────────────────────────────────────────────────


class FirejailBackend(PermissionBackend):
    name = "firejail"
    display_name = "Firejail"

    def detect_support(self) -> dict:
        return dict(_probe_backend("firejail"))

    def capabilities(self) -> dict:
        return dict(_caps_cached("firejail"))

    def launch_application(self, app_dir, cmd, env, policy):
        from niruvi.core.sandbox import Shield, ShieldConfig

        config = ShieldConfig.from_policy(policy)
        config.backend = "firejail"
        return Shield(config).run(cmd, cwd=app_dir, env=env)

    def apply_policy(self, app_id, policy, record=None, store=None) -> ApplyResult:
        if not self.available():
            return ApplyResult(False, "Firejail is not installed.")
        from niruvi.core.policy import diff_policies

        prev = SandboxPolicy.from_dict(record.sandbox_config) if record else None
        changes = diff_policies(prev, policy)["changes"] if prev else []
        return self._persist(app_id, policy, record, store, note="Applied policy", changes=changes)

    def restore_previous_policy(self, app_id, previous, record=None, store=None) -> bool:
        return self._persist(app_id, previous, record, store, note="Restored previous policy", changes=[]).ok

    def get_enforcement_status(self, policy, pending: bool = False) -> EnforcementStatus:
        if not self.available():
            return EnforcementStatus(BACKEND_UNAVAILABLE, backend=self.name)
        return _generic_status(self.name, policy, pending)


# ── Bubblewrap ───────────────────────────────────────────────────────


class BubblewrapBackend(PermissionBackend):
    name = "bwrap"
    display_name = "Bubblewrap"

    def detect_support(self) -> dict:
        return dict(_probe_backend("bwrap"))

    def capabilities(self) -> dict:
        return dict(_caps_cached("bwrap"))

    def launch_application(self, app_dir, cmd, env, policy):
        """Launch through the dedicated Bubblewrap engine (fail-closed).

        Raises :class:`~niruvi.core.bubblewrap_backend.LaunchRefusedError` when a
        restriction cannot be enforced — the caller decides how to report it;
        silently degrading to a weaker sandbox is not an option.
        """
        from niruvi.core.bubblewrap_backend import build_bwrap_sandbox

        sandbox = build_bwrap_sandbox(policy, app_dir, env)
        return sandbox.run(cmd, cwd=app_dir, env=env)

    def apply_policy(self, app_id, policy, record=None, store=None) -> ApplyResult:
        if not self.available():
            return ApplyResult(False, "Bubblewrap is not installed.")
        from niruvi.core.policy import diff_policies

        prev = SandboxPolicy.from_dict(record.sandbox_config) if record else None
        changes = diff_policies(prev, policy)["changes"] if prev else []
        return self._persist(app_id, policy, record, store, note="Applied policy", changes=changes)

    def restore_previous_policy(self, app_id, previous, record=None, store=None) -> bool:
        return self._persist(app_id, previous, record, store, note="Restored previous policy", changes=[]).ok

    def get_enforcement_status(self, policy, pending: bool = False) -> EnforcementStatus:
        if not self.available():
            return EnforcementStatus(BACKEND_UNAVAILABLE, backend=self.name)
        return _generic_status(self.name, policy, pending)


def _generic_status(backend_name: str, policy: SandboxPolicy, pending: bool) -> EnforcementStatus:
    if pending:
        return EnforcementStatus(PENDING_CHANGES, backend=backend_name)
    if not policy.enabled:
        return EnforcementStatus(NOT_SANDBOXED, backend=backend_name)
    issues = policy.validate()
    if issues:
        return EnforcementStatus(CONFIG_ERROR, detail="; ".join(issues), backend=backend_name)
    enforced = ["App is launched through " + backend_name]
    not_enforced = []
    if not policy.network:
        enforced.append("Network isolation")
    if not policy.path_to_grants():
        enforced.append("No filesystem access granted")
    denied_devices = [d for d in policy.devices if not policy.devices[d]]
    if denied_devices:
        if backend_name == "bwrap":
            enforced.append("Disallowed device nodes are absent from the sandbox /dev")
            if "microphone" in denied_devices and policy.devices.get("audio", True):
                not_enforced.append(
                    "Microphone shares ALSA nodes with audio output; disable audio to also block capture"
                )
        else:
            not_enforced.append("Device restrictions are limited on this backend: " + ", ".join(denied_devices))
    if not_enforced:
        return EnforcementStatus(
            PARTIALLY_RESTRICTED, backend=backend_name, enforced=enforced, not_enforced=not_enforced
        )
    return EnforcementStatus(SANDBOXED, backend=backend_name, enforced=enforced)


# ── Selection ────────────────────────────────────────────────────────

_BACKENDS: dict[str, type[PermissionBackend]] = {
    "shield": ShieldBackend,
    "firejail": FirejailBackend,
    "bwrap": BubblewrapBackend,
}


def available_backends() -> dict[str, PermissionBackend]:
    out: dict[str, PermissionBackend] = {}
    for name, cls in _BACKENDS.items():
        inst = cls()
        try:
            if inst.available():
                out[name] = inst
        except Exception as e:  # pragma: no cover - detection must never raise
            logger.debug("Backend %s detection failed: %s", name, e)
    out.setdefault("shield", ShieldBackend())
    return out


def get_backend(name: str, strict: bool = False) -> PermissionBackend:
    """Return a backend instance.

    With ``strict=False`` (default, used for launching) an unknown or
    unavailable backend falls back to Niruvi Shield — never to unsandboxed
    execution. With ``strict=True`` (used by the UI for capability/status
    reporting) the real instance is returned so an unavailable backend is
    reported as such instead of masquerading as Shield.
    """
    cls = _BACKENDS.get(name)
    if cls is None:
        logger.warning("Unknown backend %r — falling back to Niruvi Shield", name)
        return ShieldBackend()
    inst = cls()
    if strict:
        return inst
    if not inst.available() and name != "shield":
        logger.warning("Backend %r is unavailable — falling back to Niruvi Shield", name)
        return ShieldBackend()
    return inst


def _userns_available() -> bool:
    try:
        from niruvi.app.health_check import check_namespace_available

        return bool(check_namespace_available())
    except Exception:
        try:
            r = subprocess.run(
                ["unshare", "--user", "true"],
                capture_output=True,
                timeout=5,
            )
            return r.returncode == 0
        except Exception:
            return False


def _landlock_ok() -> bool:
    from niruvi.core.sandbox import landlock_supported

    return landlock_supported()


def backend_supports_control(name: str, control: str) -> Control:
    return get_backend(name).capability(control)


def all_backend_status() -> dict:
    out = {}
    for name, cls in _BACKENDS.items():
        inst = cls()
        info = inst.detect_support()
        out[name] = {
            "display_name": inst.display_name,
            "available": bool(info.get("available")),
            "version": info.get("version") or info.get("error"),
            "capabilities": {k: {"level": v.level, "detail": v.detail} for k, v in inst.capabilities().items()},
        }
    return out
