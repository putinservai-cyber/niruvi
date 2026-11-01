"""PolicyEngine — translate application permissions into enforcement.

Two jobs:

1.  ``validate`` / ``plan``: turn a :class:`SandboxPolicy` into the enforced
    launch plan for the selected backend (Bubblewrap first class; Firejail
    optional).  Every translation failure is explicit: the engine raises
    ``PolicyInfeasible`` and refuses to guess a weaker policy that still
    passes.  Nothing here executes commands.
2.  ``filter_environment`` / ``permission_states``: the honest data for the
    UI.  ``permission_states`` returns, for every configurable permission, the
    five states the interface must show —
    requested / approved / configured / enforced / verified — plus plain-word
    limitations.  A state is only "verified" when a real check (sandbox
    diagnostics) confirms it; the engine reports "not verified" otherwise and
    never decorates.
"""

import logging
import os
import re

from niruvi.core.policy import (
    DEVICE_KEYS,
    IPC_KEYS,
    SandboxPolicy,
)

logger = logging.getLogger(__name__)


class PolicyInfeasible(Exception):
    """The policy cannot be translated into a working launch plan."""


class PolicyValidationError(PolicyInfeasible):
    pass


_PATH_FORBIDDEN = re.compile(r"[\x00-\x1f]")


def validate_policy(policy: SandboxPolicy, strict_launch: bool = False) -> list[str]:
    """All problems that would make the policy unmappable to enforcement."""
    issues = list(policy.validate())
    if _PATH_FORBIDDEN.search(policy.app_id or ""):
        issues.append("app_id contains control characters")
    for grant in policy.custom_paths:
        raw = grant.path if hasattr(grant, "path") else str(grant)
        if _PATH_FORBIDDEN.search(raw):
            issues.append(f"custom path contains control characters: {raw!r}")
    for grant in policy.custom_paths:
        mode = getattr(grant, "mode", "ro")
        if mode not in ("ro", "rw"):
            issues.append(f"custom grant mode must be ro/rw, not {mode!r}")
    return issues


def filter_environment(policy: SandboxPolicy, env: dict) -> dict:
    """The launch environment after policy filtering.

    Only the sandbox's compiled allowlist (niruvi's ALWAYS_KEEP_ENV/_REQUIRED_
    ENV: PATH, HOME, XDG metadata, locale, display metadata plus anything the
    specific channel policy needs) is inherited from the host.  Sensitive
    tokens (SSH/cloud/proxy credentials) are never inherited; a trusted
    caller override may set them explicitly, but a policy-withheld channel
    variable is still stripped so a blocked channel is also *invisible*, not
    merely unreachable.
    """
    from niruvi.core.sandbox import ALWAYS_KEEP_ENV, HARDENED_ENV, _REQUIRED_ENV

    keep = set(ALWAYS_KEEP_ENV) | set(_REQUIRED_ENV)
    ipc = policy.ipc or {}
    devices = policy.devices or {}

    def strip(kept: dict) -> dict:
        if not ipc.get("x11", False):
            kept.pop("DISPLAY", None)
        if not ipc.get("wayland", False):
            kept.pop("WAYLAND_DISPLAY", None)
        if not ipc.get("dbus_session", False):
            kept.pop("DBUS_SESSION_BUS_ADDRESS", None)
        if not devices.get("audio", False) and not devices.get("microphone", False):
            for key in ("PULSE_SERVER", "PULSE_COOKIE", "PIPEWIRE_RUNTIME_DIR", "PULSE_RUNTIME_PATH"):
                kept.pop(key, None)
        kept.pop("PYTHONPATH", None)
        return kept

    kept = {k: v for k, v in os.environ.items() if k in keep}
    kept = strip(kept)
    if env:
        kept.update(env)
        kept = strip(kept)  # overrides must not resurrect withheld channels
    if not kept.get("HOME"):
        kept["HOME"] = os.path.expanduser("~")
    if not kept.get("PATH"):
        kept["PATH"] = "/usr/local/bin:/usr/bin:/bin"
    if policy.hardening:
        for k, v in HARDENED_ENV.items():
            kept.setdefault(k, v)
    kept.setdefault("GTK_USE_PORTAL", "1")
    return kept


# ── permission states ─────────────────────────────────────────────────


def permission_states(
    policy: SandboxPolicy,
    backend,
    verification: dict | None = None,
) -> list[dict]:
    """Per-permission state rows for the UI.

    Each row has: id, title, group, state (not_requested / denied / allowed /
    partially_enforced / unsupported / failed / verified), configured(bool),
    enforced(bool), verified(bool|None), explanation.

    ``verification`` maps permission ids to one of "verified"/"failed"; None
    or missing means "not verified" (never guessed).
    """
    verification = verification or {}
    states: list[dict] = []
    caps = backend.capabilities()

    def cap_level(control: str) -> str:
        cap = caps.get(control)
        if cap is None:
            return "unsupported"
        return cap.level

    def row(pid: str, title: str, group: str, control: str,
            requested: bool, approved: bool, configured: bool):
        level = cap_level(control)
        enforced = configured and level in ("supported", "partial")
        if not requested:
            state = "not_requested"
        elif not approved:
            state = "denied"
        elif not configured:
            state = "denied"
        elif level == "unsupported":
            state = "unsupported"
        elif requested and approved and configured and not enforced:
            state = "failed"
        else:
            state = "allowed"
        if state == "allowed" and level == "partial":
            state = "partially_enforced"
        ver = verification.get(pid)
        if state in ("failed",):
            verified = False
        elif ver == "verified":
            verified = state in ("allowed", "partially_enforced")
        elif ver == "failed":
            verified = False
            state = "failed"
        else:
            verified = None
            if state == "allowed":
                state = "allowed"  # not "verified": nothing confirmed this run
        states.append(
            {
                "id": pid,
                "title": title,
                "group": group,
                "state": state,
                "configured": configured,
                "enforced": enforced,
                "verified": verified,
                "explanation": _explanation(pid, cap_level(control), policy),
            }
        )

    from niruvi.core.policy import _DIR_KEYS  # noqa: F401 (internal)

    file_rows = [
        ("home", "Home directory", "FILES", "filesystem", policy.home_access != "none",
         policy.home_access != "none", bool(policy.home_access and policy.home_access != "none")),
    ]
    for key in _DIR_KEYS:
        mode = policy.dirs.get(key, "none")
        file_rows.append(
            (f"dir_{key}", key.title() + " folder", "FILES", "filesystem",
             mode != "none", mode != "none", mode != "none")
        )
    file_rows.append(
        ("removable", "Removable storage", "FILES", "filesystem",
         policy.removable != "none", policy.removable != "none", policy.removable != "none")
    )
    for pid, title, group, control, requested, approved, configured in file_rows:
        row(pid, title, group, control, requested, approved, configured)

    row("network", "Network access", "NETWORK", "network",
        True, policy.network, policy.network)

    titles = {
        "gpu": "Graphics acceleration",
        "audio": "Sound output",
        "microphone": "Microphone",
        "camera": "Camera",
        "usb": "USB devices",
        "input": "Input devices",
    }
    for key in DEVICE_KEYS:
        value = bool(policy.devices.get(key, False))
        row(f"device_{key}", titles[key], "DEVICES", "devices", True, value, value)

    ipc_titles = {
        "wayland": "Wayland display",
        "x11": "X11 display",
        "dbus_session": "Session D-Bus",
        "dbus_system": "System D-Bus",
        "printing": "Printing",
    }
    for key in IPC_KEYS:
        if key not in ipc_titles:
            continue  # private-ish channels shown in SYSTEM
        value = bool(policy.ipc.get(key, False))
        row(f"ipc_{key}", ipc_titles[key], "DESKTOP INTEGRATION", "ipc", True, value, value)

    desktop_titles = {
        "clipboard": "Clipboard (via display servers)",
    }
    display_ok = bool(policy.ipc.get("wayland", False) or policy.ipc.get("x11", False))
    for key, title in desktop_titles.items():
        row(
            f"desktop_{key}", title, "DESKTOP INTEGRATION", "ipc",
            True, display_ok, display_ok,
        )

    sys_titles = {
        "private_tmp": "Private /tmp",
        "hardening": "Process hardening",
        "seccomp": "Syscall filtering",
        "use_namespace": "Namespace process isolation",
    }
    for key in ("private_tmp", "hardening", "seccomp", "use_namespace"):
        value = bool(getattr(policy, key, False))
        row(f"system_{key}", sys_titles[key], "SYSTEM", "resources" if key in ("hardening", "seccomp") else "ipc", True, value, value)

    return states


def _explanation(pid: str, level: str, policy: SandboxPolicy) -> str:
    base = {
        "supported": "This backend enforces this permission reliably.",
        "partial": "Enforced with documented caveats (device nodes, socket scope).",
        "unsupported": "This backend cannot enforce this permission; it stays disabled.",
    }.get(level, "")
    if pid in ("network",) and not policy.network:
        base += " Blocking uses a network namespace, which cuts off all DNS, sockets and raw IP — it is coarser than firejail --net filters."
    if pid == "device_microphone":
        base += " Allowing access does not guarantee that the app will use the device, and ALSA equipment currently cannot split output from capture."
    return base


def summary_state(states: list[dict]) -> str:
    """UI summary: protected / partially_protected / not_protected / failed.

    Derived only from the per-permission states the engine computed — not a
    decoration layer.
    """
    enforceable = [
        s for s in states
        if s["configured"] and s["explanation"]  # every row participates
    ]
    any_failed = any(s["state"] == "failed" for s in states)
    enforced = [s for s in enforceable if s["enforced"]]
    unsupported = [s for s in enforceable if s["state"] == "unsupported"]
    if any_failed:
        return "failed"
    if not enforced:
        return "not_protected"
    if unsupported or any(s["state"] == "partially_enforced" for s in enforceable):
        return "partially_protected"
    return "protected"
