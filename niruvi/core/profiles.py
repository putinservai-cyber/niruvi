"""Security profiles: STANDARD / STRICT / CUSTOM / DEVELOPER.

A profile is a named baseline :class:`~niruvi.core.policy.SandboxPolicy`.
``switch_profile`` computes the concrete target policy for an app and reports
**which restrictions get relaxed** so the change is visible before applying.
CUSTOM is never generated here — it means "the user's own stored policy".

Rules encoded as code, not convention:

* DEVELOPER is explicitly weaker and flagged; :func:`default_profile_name`
  refuses to return it, so it can never become the default profile.
* Switching profiles never silently rewrites a CUSTOM policy — the caller
  must confirm, and :func:`switch_profile` returns the relaxed list first.
* ``policy.profile`` records which profile produced a stored policy; unknown
  values are treated as "custom" rather than crashing on import.

STANDARD is practical: desktop display integration, sound, network,
standard folders readable... but NO write access to the home directory by
default (sandbox data lives in portable .home/.config when the app opts in;
personal files are opt-in grants).  STRICT disables network and restricts
further.  DEVELOPER keeps most access so a debugger can inspect state.
"""

from copy import deepcopy

from niruvi.core.policy import (
    DEVICE_KEYS,
    IPC_KEYS,
    SandboxPolicy,
)

PROFILE_SCHEMA_VERSION = 2

PROFILE_NAMES = ("standard", "strict", "custom", "developer")

PROFILE_LABELS = {
    "standard": "Standard",
    "strict": "Strict",
    "custom": "Custom",
    "developer": "Developer (weaker — debugging only)",
}


def _base(app_id: str = "", backend: str = "bwrap") -> SandboxPolicy:
    p = SandboxPolicy(app_id=app_id, backend=backend)
    p.profile = "standard"
    return p


def preset_standard(app_id: str = "", backend: str = "bwrap") -> SandboxPolicy:
    """Practical default: workable desktop integration, no free hand on files."""
    p = _base(app_id, backend)
    p.enabled = True
    p.hardening = True
    p.seccomp = True
    p.landlock = True
    p.use_namespace = True
    p.home_access = "none"
    p.dirs = {k: "ro" for k in ("downloads", "documents", "pictures", "videos", "music", "desktop")}
    p.removable = "ro"
    p.custom_paths = []
    p.network = True
    p.devices = {k: (k in ("gpu", "audio")) for k in DEVICE_KEYS}
    p.ipc = {
        "wayland": True,
        "x11": True,
        "dbus_session": True,
        "dbus_system": False,
        "ipc": False,
        "printing": True,
        "ssh_agent": False,
    }
    p.private_tmp = True
    p.portable_home = False
    p.portable_config = False
    p.memory_limit = ""
    p.cpu_limit = ""
    p.timeout = 0
    return p


def preset_strict(app_id: str = "", backend: str = "bwrap") -> SandboxPolicy:
    """Hardened: network off, filesystem nearly closed, minimal integration."""
    p = preset_standard(app_id, backend)
    p.profile = "strict"
    p.home_access = "none"
    p.dirs = {k: "none" for k in ("downloads", "documents", "pictures", "videos", "music", "desktop")}
    p.removable = "none"
    p.network = False
    p.devices = {k: False for k in DEVICE_KEYS}
    p.devices["gpu"] = True  # a window needs GL on most desktops; audio still off
    p.ipc["dbus_system"] = False
    p.ipc["printing"] = False
    p.private_tmp = True
    return p


def preset_developer(app_id: str = "", backend: str = "bwrap") -> SandboxPolicy:
    """Explicitly weaker, for debugging: full home, network, IPC, debugger paths."""
    p = preset_standard(app_id, backend)
    p.profile = "developer"
    p.home_access = "rw"
    p.dirs = {k: "rw" for k in ("downloads", "documents", "pictures", "videos", "music", "desktop")}
    p.removable = "rw"
    p.network = True
    p.devices = {k: True for k in DEVICE_KEYS}
    p.ipc = {k: True for k in IPC_KEYS}
    p.private_tmp = False
    return p


def build_custom(from_policy: SandboxPolicy | None = None) -> SandboxPolicy:
    """Mark a policy as CUSTOM (user-managed)."""
    p = deepcopy(from_policy) if from_policy is not None else SandboxPolicy()
    p.profile = "custom"
    return p


PROFILE_PRESETS = {
    "standard": preset_standard,
    "strict": preset_strict,
    "developer": preset_developer,
}

DEFAULT_PROFILE_NAME = "standard"


def default_profile_name() -> str:
    """The default profile.  DEVELOPER is structurally excluded."""
    return DEFAULT_PROFILE_NAME


def is_default_profile(name: str) -> bool:
    return name == DEFAULT_PROFILE_NAME


def validate_profile_name(name: str) -> str:
    """Coerce a stored/imported profile name to a known safe value."""
    name = (name or "").strip().lower()
    if name in PROFILE_NAMES and name != "developer":
        return name
    if name == "developer":
        return "developer"  # valid for import as an explicit choice, not default
    return "custom"


def build_profile(name: str, app_id: str = "", backend: str = "bwrap") -> SandboxPolicy:
    """Instantiate a profile by its (validated) name."""
    name = validate_profile_name(name)
    if name == "custom":
        p = build_custom()
        p.app_id = app_id
        p.backend = backend
        p.profile = "custom"
        return p
    builder = PROFILE_PRESETS.get(name)
    if builder is None:
        raise ValueError(f"Unknown profile: {name}")
    return builder(app_id, backend)


def profile_field_defaults() -> dict:
    return {"profile": "standard"}


def describe_profile(name: str) -> str:
    p = build_profile(name)
    lines = [
        f"Profile: {PROFILE_LABELS.get(name, name)}",
        f"Home: {'read/write' if p.home_access == 'rw' else 'read-only' if p.home_access == 'ro' else 'no access'}",
        f"Standard folders: {sorted({m for m in p.dirs.values()})}",
        f"Removable storage: {p.removable}",
        f"Network: {'allowed' if p.network else 'blocked'}",
        f"Devices allowed: {[k for k in DEVICE_KEYS if p.devices.get(k)]}",
        f"Desktop channels: {[k for k in IPC_KEYS if p.ipc.get(k)]}",
        f"Private /tmp: {p.private_tmp}",
    ]
    return "\n".join(lines)


def switch_profile(current: SandboxPolicy, target: str, app_id: str = "", backend: str | None = None) -> dict:
    """Compute a profile switch WITHOUT writing anything.

    Returns ``{"policy": SandboxPolicy, "relaxed": list[str], "tightened":
    list[str], "profile": target}``.  ``relaxed`` lists every restriction the
    switch weakens so the UI can show it before applying; the caller must
    confirm — nothing is applied here.  DEVELOPER targets are legal but always
    appear in ``relaxed`` (weaker by design).
    """
    target = validate_profile_name(target)
    new = build_profile(target, app_id or current.app_id, backend or current.backend)
    relaxed = restriction_deltas(current, new)["relaxed"]
    if target == "developer":
        relaxed.insert(
            0, "DEVELOPER profile is explicitly weaker: home, network, devices and IPC open up for debugging."
        )
    return {
        "policy": new,
        "relaxed": relaxed,
        "tightened": restriction_deltas(current, new)["tightened"],
        "profile": target,
    }


def _access_order() -> dict:  # from least to most restrictive
    return {"rw": 0, "ro": 1, "none": 2}


def restriction_deltas(current: SandboxPolicy, target: SandboxPolicy) -> dict:
    """List every switch relaxation ('relaxed') and tightening ('tightened')."""
    relaxed: list[str] = []
    tightened: list[str] = []
    order = _access_order()

    def cmp_access(cur: str, new_t: str, label: str):
        if order.get(new_t, 1) < order.get(cur, 1):
            relaxed.append(f"{label}: {cur} -> {new_t}")
        elif order.get(new_t, 1) > order.get(cur, 1):
            tightened.append(f"{label}: {cur} -> {new_t}")

    cmp_access(current.home_access, target.home_access, "Home directory")
    for key in current.dirs:
        if key in target.dirs:
            cmp_access(current.dirs[key], target.dirs[key], f"{key.title()} folder")
    cmp_access(current.removable, target.removable, "Removable storage")

    if current.network != target.network:
        if target.network:
            relaxed.append("Network access: blocked -> allowed")
        else:
            tightened.append("Network access: allowed -> blocked")
    for key in DEVICE_KEYS:
        cu = bool(current.devices.get(key))
        tv = bool(target.devices.get(key))
        if cu != tv:
            label = key
            if tv:
                relaxed.append(f"Device '{label}': blocked -> allowed")
            else:
                tightened.append(f"Device '{label}': allowed -> blocked")
    for key in IPC_KEYS:
        cu = bool(current.ipc.get(key))
        tv = bool(target.ipc.get(key))
        if cu != tv:
            if tv:
                relaxed.append(f"Host channel '{key}': blocked -> allowed")
            else:
                tightened.append(f"Host channel '{key}': allowed -> blocked")

    if current.private_tmp != target.private_tmp:
        if not target.private_tmp:
            relaxed.append("Private /tmp: shared with the host session")
        else:
            tightened.append("Private /tmp: enabled")
    if current.portable_home != target.portable_home:
        if target.portable_home:
            tightened.append("Portable home enabled")
        else:
            relaxed.append("Portable home disabled (real home visible per filesystem grants)")

    return {"relaxed": relaxed, "tightened": tightened}
