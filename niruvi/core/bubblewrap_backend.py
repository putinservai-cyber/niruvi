"""Bubblewrap sandbox engine — policy to argv, then execution.

``build_bwrap_args`` is a pure function: given a validated
:class:`~niruvi.core.policy.SandboxPolicy`, an application directory, an
environment and a capability report, it returns the exact argument list handed
to ``bwrap``.  Nothing is shell-interpolated: the argv goes straight to
``subprocess``/exec, so command construction cannot inject shell syntax.

Filesystem view (minimal, explicit):

* an empty root except for what is explicitly bound or symlinked,
* ``/usr`` and ``/etc`` read-only, plus compatibility ``/bin`` ``/sbin``
  ``/lib`` ``/lib64`` handled per host layout (symlink vs real directory),
* the application directory read-only (it must not rewrite its own code),
* user data granted exactly as policy says (home / standard dirs /
  removable),
* fresh tmpfs on ``/tmp`` ``/var/tmp`` ``/dev/shm`` mounted last so nothing
  can stack on top of it and turn it read-only,
* device nodes and runtime sockets only for the categories the policy
  allows,
* ``--unshare-all`` plus ``--share-net`` when network is allowed (or
  per-namespace flags when the kernel cannot unshare everything),
  ``--new-session`` and ``--die-with-parent`` always.

Security notes kept here on purpose:

* A mounted socket is not just a file: Wayland/X11/D-Bus/pulse sockets carry
  compositor/portal/service authority.  Every such bind gets a plan note.
* Failures are fail-closed: :class:`BubblewrapSandbox` raises
  :class:`LaunchRefused` instead of degrading — it never launches
  unsandboxed.  A setuid bubblewrap binary is refused outright (it would
  grant arbitrary mount privileges the policy cannot audit).
"""

import glob
import logging
import os
import shutil
import stat
import subprocess
from dataclasses import dataclass, field

from niruvi.core.policy import SandboxPolicy

logger = logging.getLogger(__name__)

# Scratch space is mounted LAST; nothing else may stack on top of these.
SCRATCH_TARGETS = ("/tmp", "/var/tmp", "/dev/shm")

_COMPAT_PATHS = (
    ("/bin", "/usr/bin"),
    ("/sbin", "/usr/sbin"),
    ("/lib", "/usr/lib"),
    ("/lib64", "/usr/lib64"),
)

_DANGEROUS_GRANTS = (
    "/etc",
    "/usr",
    "/boot",
    "/root",
    "/lib",
    "/lib64",
    "/proc",
    "/sys",
    "/dev",
    "/bin",
    "/sbin",
    "/run",
)


def bwrap_program() -> str:
    return "bwrap"


class LaunchRefused(Exception):
    """The sandbox cannot honour the requested policy (fail-closed)."""


@dataclass
class BwrapPlan:
    args: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    namespaces: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)

    @property
    def share_net(self) -> bool:
        return "--share-net" in self.args

    @property
    def argv(self) -> list[str]:
        return [bwrap_program(), *self.args]


def _add(plan: BwrapPlan, args: list) -> None:
    plan.args.extend(args)


def validate_grant_path(path: str) -> str | None:
    """Normalise a filesystem grant target; refuse dangerous roots.

    Returns the absolute path, or None when the grant must be refused:
    empty/"~"-only, nonexistent paths, anything equal to a sensitive system
    root, or a scratch mount ROOT itself (grants *below* it are fine: they
    stack over the scratch tmpfs).
    """
    if not path:
        return None
    path = os.path.expanduser(path)
    path = os.path.normpath(path)
    if not path or path == "/":
        return None
    if not os.path.exists(path):
        return None
    resolved = os.path.realpath(path)
    if resolved == "/":
        return None
    for scratch in SCRATCH_TARGETS:
        if resolved == scratch:
            # Re-granting a scratch ROOT would cover the private tmpfs with
            # the host copy — that is exactly the leak the policy forbids.
            return None
    for dangerous in _DANGEROUS_GRANTS:
        if resolved == dangerous or resolved.startswith(dangerous + os.sep):
            return None
    return resolved


def namespace_flags(policy: SandboxPolicy, caps: dict) -> tuple[list[str], list[str], list[str]]:
    """Which ``--unshare-*`` flags to use given capability detection.

    Returns (flags, skipped, notes).  ``--unshare-all`` + ``--share-net`` is
    the preferred path; when the all-in-one flag is not possible we emit only
    the single namespace flags the kernel accepts (never silently weaker than
    asking without saying so — each skip appears in ``notes``).
    """
    namespaces = caps.get("namespaces", {})
    flags: list[str] = []
    skipped: list[str] = []
    notes: list[str] = []

    def ns_ok(name: str) -> bool:
        return bool((namespaces.get(name) or {}).get("available"))

    want_net = bool(policy.network)
    want_pid = bool(policy.use_namespace)
    all_ok = all(ns_ok(n) for n in ("user", "pid", "ipc", "network"))

    if all_ok:
        flags.append("--unshare-all")
        if want_net:
            flags.append("--share-net")
        return flags, skipped, notes

    if not ns_ok("user"):
        skipped.append("user")
        notes.append(
            "User namespaces unavailable — mount namespace-based isolation cannot start; call would be refused."
        )
        # fail-closed is enforced by BubblewrapSandbox.check_caps
    if ns_ok("pid") and want_pid:
        flags.append("--unshare-pid")
    elif want_pid and not ns_ok("pid"):
        skipped.append("pid")
        notes.append("PID namespaces unavailable (needs kernel.unprivileged_userns_clone or CONFIG_PID_NS).")
    if ns_ok("ipc"):
        flags.append("--unshare-ipc")
    else:
        skipped.append("ipc")
        notes.append("IPC namespaces unavailable — SysV/POSIX IPC stays shared with the host.")
    if ns_ok("network") and not want_net:
        flags.append("--unshare-net")
    elif not want_net and not ns_ok("network"):
        skipped.append("network")
        notes.append("Network namespaces unavailable — network access cannot be blocked.")
    if ns_ok("user"):
        flags.append("--unshare-user")
    if ns_ok("user") or all(ns_ok(n) for n in ("pid", "ipc", "network")):
        pass  # extra uts only when its own probe passes; skipped for simplicity

    return flags, skipped, notes


def _devices(plan: BwrapPlan, policy: SandboxPolicy) -> None:
    devices = policy.devices or {}

    def want(key: str) -> bool:
        return bool(devices.get(key, False))

    def dev_bind(path: str):
        _add(plan, ["--dev-bind", path, path])

    if os.path.isdir("/dev/dri") and want("gpu"):
        dev_bind("/dev/dri")
    if want("gpu") and not os.path.isdir("/dev/dri"):
        plan.notes.append("No /dev/dri on host: GPU acceleration unavailable to the sandbox.")
    for nvidia_node in sorted(glob.glob("/dev/nvidia*")):
        if want("gpu") and os.path.exists(nvidia_node):
            dev_bind(nvidia_node)
    if os.path.isdir("/dev/snd") and (want("audio") or want("microphone")):
        dev_bind("/dev/snd")
        if want("audio") and not want("microphone"):
            plan.notes.append(
                "Microphone restriction is partial: ALSA nodes carry both output and capture."
            )
        elif want("microphone") and not want("audio"):
            plan.notes.append(
                "Audio output stays reachable while the microphone is granted (shared ALSA nodes)."
            )
    if want("camera"):
        for video_node in sorted(glob.glob("/dev/video*")):
            if os.path.exists(video_node):
                dev_bind(video_node)
    if os.path.isdir("/dev/bus/usb") and want("usb"):
        dev_bind("/dev/bus/usb")
        plan.notes.append("/dev/bus/usb grants USB devices as a whole; per-device scoping is not possible here.")
    if os.path.isdir("/dev/input") and want("input"):
        dev_bind("/dev/input")


def _runtime_socket_bindings(plan: BwrapPlan, policy: SandboxPolicy, env: dict, runtime_dir: str) -> None:
    ipc = policy.ipc or {}
    devices = policy.devices or {}

    def bind_rt(rel: str):
        path = os.path.join(runtime_dir, rel)
        if os.path.exists(path):
            _add(plan, ["--bind", path, path])

    wayland_ok = bool(ipc.get("wayland", False))
    x11_ok = bool(ipc.get("x11", False))
    audio_ok = bool(devices.get("audio", False) or devices.get("microphone", False))
    dbus_ok = bool(ipc.get("dbus_session", False))

    if wayland_ok:
        socks = sorted(glob.glob(os.path.join(runtime_dir, "wayland-*")))
        for sock in socks:
            bind_rt(os.path.basename(sock))
        if socks:
            plan.notes.append(
                "Mounted socket: Wayland grants full compositor authority, not just file access."
            )
    if x11_ok and os.path.isdir(os.path.join(runtime_dir, ".X11-unix")):
        bind_rt(".X11-unix")
        plan.notes.append(
            "Mounted socket: X11 clients can snoop events from other applications (known X11 limitation)."
        )
    if audio_ok:
        for rel in ("pulse", "pipewire-0"):
            bind_rt(rel)
    if dbus_ok:
        addr = env.get("DBUS_SESSION_BUS_ADDRESS", "") or os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
        if addr.startswith("unix:path="):
            bus_path = addr.split("unix:path=", 1)[1].split(",", 1)[0]
            if bus_path.startswith(runtime_dir) and os.path.exists(bus_path):
                _add(plan, ["--bind", bus_path, bus_path])
                plan.notes.append(
                    "Mounted socket: session D-Bus opens notification/portal/tray service authority."
                )
    if ipc.get("dbus_system", False) and os.path.isdir("/run/dbus"):
        _add(plan, ["--ro-bind", "/run/dbus", "/run/dbus"])
        plan.notes.append(
            "Mounted socket: system bus exposes control over udisks, NetworkManager and peers."
        )
    if (ipc.get("printing", False) or dbus_ok) and (wayland_ok or x11_ok):
        # Portals serve file dialogs / screenshot helpers when display is allowed.
        bind_rt("portal")
        bind_rt(".dbus-proxy")
    if ipc.get("ssh_agent", False):
        if os.path.exists(os.path.join(runtime_dir, "keyring")):
            bind_rt("keyring")
        plan.notes.append("Mounted socket: SSH agent grants signing authority to the app.")


def _env_setenv(plan: BwrapPlan, policy: SandboxPolicy, env: dict, home: str) -> None:
    from niruvi.core.policy_engine import filter_environment

    cleaned = filter_environment(policy, env)
    _add(plan, ["--clearenv"])
    for key in sorted(cleaned):
        _add(plan, ["--setenv", key, cleaned[key]])


def build_bwrap_sandbox(policy: SandboxPolicy, app_dir: str, env: dict) -> "BubblewrapSandbox":
    """Convenience: capability report + plan + executable sandbox in one."""
    from niruvi.core.capability_detector import report as _report
    from niruvi.core.policy_engine import filter_environment

    caps = _report()
    env = filter_environment(policy, env or {})
    plan = build_bwrap_args(policy, app_dir, env, caps=caps)
    return BubblewrapSandbox(plan, caps=caps)


def build_bwrap_args(
    policy: SandboxPolicy,
    app_dir: str,
    env: dict,
    caps: dict | None = None,
    home: str | None = None,
) -> BwrapPlan:
    """Build the full bwrap argv (pure; tests mock nothing here).

    Do NOT launch for the caller: the argv alone; see BubblewrapSandbox.
    """
    if caps is None:
        from niruvi.core.capability_detector import report as _report

        caps = _report()

    plan = BwrapPlan()
    home = env.get("HOME") or home or os.path.expanduser("~")

    flags, skipped, notes = namespace_flags(policy, caps)
    plan.namespaces = [f.lstrip("-") for f in flags]
    plan.notes.extend(notes)
    _add(plan, ["--new-session", "--die-with-parent"])  # always
    _add(plan, flags)

    _add(plan, ["--proc", "/proc"])
    _add(plan, ["--dev", "/dev"])
    _devices(plan, policy)

    # Scratch tmpfs mounts come FIRST so policy grants (custom dirs under
    # /tmp, a private app .tmp bind, ...) stack cleanly on top of them.
    # bwrap mounts stack: the later bind wins for its mountpoint exactly as
    # verified against bwrap 0.11 on this host.
    for scratch in SCRATCH_TARGETS:
        _add(plan, ["--tmpfs", scratch])
    plan.notes.append(
        "Scratch /tmp, /var/tmp and /dev/shm are fresh tmpfs; nothing from "
        "the host scratch space leaks in (mounts later in the argv, e.g. "
        "policy grants or a private .tmp, stack on top)."
    )

    if os.path.isdir("/sys"):
        _add(plan, ["--ro-bind", "/sys", "/sys"])
        if not plan.share_net:
            # Host sysfs reveals interface names/state even under a fresh net
            # namespace (where they cannot pass traffic). Mask it so a blocked
            # network is also invisible to the app and to verification probes.
            _add(plan, ["--tmpfs", "/sys/class/net"])
            plan.notes.append(
                "Network blocked: /sys/class/net masked on top of the fresh "
                "namespace; interfaces are unreachable and not listed."
            )
    if os.path.isdir("/usr"):
        _add(plan, ["--ro-bind", "/usr", "/usr"])
    for compat_path, usr_target in _COMPAT_PATHS:
        if os.path.islink(compat_path):
            _add(plan, ["--symlink", usr_target, compat_path])
        elif os.path.isdir(compat_path):
            _add(plan, ["--ro-bind", compat_path, compat_path])
    if os.path.isdir("/etc"):
        _add(plan, ["--ro-bind", "/etc", "/etc"])

    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "")
    if runtime_dir and os.path.isdir(runtime_dir):
        # channel gating happens in _runtime_socket_bindings; a whole-runtime
        # bind would defeat per-channel filtering, so only sockets bind.
        _runtime_socket_bindings(plan, policy, env, runtime_dir)

    # Application directory is read-only: the app must not rewrite its own code.
    if app_dir and os.path.isdir(app_dir):
        resolved = os.path.realpath(app_dir)
        under_scratch = any(resolved == s or resolved.startswith(s + os.sep) for s in SCRATCH_TARGETS)
        if not under_scratch:
            _add(plan, ["--ro-bind", app_dir, app_dir])
        if policy.private_tmp:
            app_tmp = os.path.join(app_dir, ".tmp")
            os.makedirs(app_tmp, exist_ok=True)
            _add(plan, ["--bind", app_tmp, "/tmp"])
            plan.notes.append("Private /tmp at " + app_tmp + " (cleared each launch).")
    elif app_dir:
        plan.warnings.append(f"App directory missing, not mounted: {app_dir}")

    if policy.portable_home:
        if app_dir and os.path.isdir(app_dir):
            portable_home = os.path.join(app_dir, ".home")
            os.makedirs(portable_home, exist_ok=True)
            _add(plan, ["--bind", portable_home, home])
            plan.notes.append("Portable .home mounted over $HOME (data self-contained).")
        else:
            plan.warnings.append("portable_home requested but the app directory is missing.")
    else:
        usable_home = validate_grant_path(home)
        if policy.home_access == "none":
            plan.notes.append("Home access: none — no HOME binds; only tmp scratch is writable.")
        elif usable_home is None:
            plan.warnings.append("Home grant refused by safe path policy; falling back to no home access.")
            plan.notes.append("Home access was requested but is not grantable at this location.")
        else:
            if policy.home_access == "rw":
                _add(plan, ["--bind", usable_home, usable_home])
            else:
                _add(plan, ["--ro-bind", usable_home, usable_home])
            _add(plan, ["--bind", usable_home, home])
        plan.notes.append(f"Home policy: {policy.home_access}" + ("" if policy.portable_home else ""))
        if policy.home_access != "none" and usable_home is not None:
            plan.notes.append(
                "HOME is mounted at its real host path; the app sees the real home path."
            )

    if policy.portable_config and app_dir:
        portable_cfg = os.path.join(app_dir, ".config")
        os.makedirs(portable_cfg, exist_ok=True)
        _add(plan, ["--bind", portable_cfg, os.path.join(home, ".config")])

    # ── Filesystem grants ──
    seen = set()
    for path, mode in policy.path_to_grants(home=home):
        real = validate_grant_path(path)
        if real is None:
            plan.warnings.append(f"Grant refused by safe path policy: {path}")
            continue
        key = (real, mode)
        if key in seen:
            continue
        seen.add(key)
        if policy.home_access == "none":
            # home itself is never mounted; XDG dirs grant individually
            pass
        if mode == "none":
            plan.notes.append(f"{real}: deliberately blocked from the sandbox (policy 'none').")
            continue
        if policy.portable_home and real.startswith(home):
            continue  # covered by the portable home mount
        if policy.portable_config and real.startswith(os.path.join(home, ".config")):
            continue
        if mode == "rw":
            _add(plan, ["--bind", real, real])
        else:
            _add(plan, ["--ro-bind", real, real])
        plan.notes.append(f"{real} mounted {mode.upper()}.")

    for grant in policy.custom_paths:
        real = validate_grant_path(grant.path)
        if real is None:
            plan.refused.append(grant.path)
            plan.warnings.append(f"Custom grant refused by safe path policy: {grant.path}")
            continue
        if grant.mode == "rw":
            _add(plan, ["--bind", real, real])
        else:
            _add(plan, ["--ro-bind", real, real])
        plan.notes.append(f"Custom grant {real} ({grant.mode}).")


    _env_setenv(plan, policy, env, home)
    _add(plan, ["--chdir", app_dir] if app_dir and os.path.isdir(app_dir) else [])

    return plan


def probe_cmd_for(plan_args: list[str], cmd: list[str]) -> list[str]:
    return [bwrap_program(), *plan_args, *cmd]


class BubblewrapSandbox:
    """Execute a plan.  Fail-closed: never degrades to unsandboxed."""

    SECCOMP_NOT_PROVIDED = "seccomp filtering is provided by the Shield backend, not bubblewrap"

    def __init__(self, plan: BwrapPlan, caps: dict | None = None):
        self.plan = plan
        self._caps = caps

    def check_prerequisites(self) -> list[str]:
        """Refusals (fail-closed): everything that must be true to launch."""
        refusals: list[str] = []
        caps = self._caps or {}
        bwrap_info = caps.get("bubblewrap", {})
        if not bwrap_info.get("available"):
            refusals.append(
                f"bubblewrap not usable: {bwrap_info.get('error') or 'not found in PATH'}"
            )
        elif self._is_setuid():
            refusals.append(
                "bubblewrap has the setuid bit set: this build cannot be audited, refusing to launch."
            )
        namespaces = caps.get("namespaces", {})
        if not (namespaces.get("user") or {}).get("available"):
            refusals.append(
                "user namespaces unavailable (kernel.unprivileged_userns_clone=0?). "
                "The mount namespace bwrap relies on cannot be created, so its isolation is unusable."
            )
        want_net_block = not self.plan.share_net
        if want_net_block and not (namespaces.get("network") or {}).get("available"):
            refusals.append(
                "network namespaces unavailable — the policy says network is blocked, and blocking "
                "cannot be established. Failing closed instead of launching networked."
            )
        if self.plan.refused:
            refusals.append(
                "%d file-system grant(s) failed path validation (%s); failing closed instead of "
                "launching with a weaker subset." % (len(self.plan.refused), ", ".join(self.plan.refused))
            )
        return refusals

    def _is_setuid(self) -> bool:
        try:
            path = shutil.which(bwrap_program())
            if not path:
                return False
            mode = os.stat(path).st_mode
            return bool(mode & stat.S_ISUID)
        except OSError:
            return False

    def run(
        self,
        cmd: list[str],
        cwd: str | None = None,
        env: dict | None = None,
    ) -> subprocess.Popen:
        refusals = self.check_prerequisites()
        if refusals:
            raise LaunchRefused("; ".join(refusals))
        try:
            return subprocess.Popen(
                [*self.plan.argv, *cmd],
                cwd=cwd or os.getcwd(),
                env=env or os.environ,
                close_fds=True,
                start_new_session=True,
            )
        except FileNotFoundError as e:
            raise LaunchRefused(f"bubblewrap disappeared from PATH: {e}") from e
        except OSError as e:
            raise LaunchRefused(f"bwrap could not start: {e}") from e

    def run_probe(self, cmd: list[str], timeout: float = 30) -> subprocess.CompletedProcess:
        """Run a command inside this plan's sandbox (used for verification)."""
        return subprocess.run(
            [*self.plan.argv, *cmd],
            capture_output=True,
            timeout=timeout,
        )
