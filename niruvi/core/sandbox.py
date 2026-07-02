"""Niruvi Shield — sandbox backend for AppImages.

Supports multiple backends:
  - SHIELD: native process hardening (rlimits, mlockall, ptrace disable)
           + portable .home/.config (like AppManager)
  - FIREJAIL: external sandbox via firejail(1)
  - BUBBLEWRAP: external sandbox via bubblewrap(1)

Usage:
    from niruvi.core.sandbox import Shield, ShieldConfig, SandboxBackend
    config = ShieldConfig(portable_home=True, portable_config=True)
    sb = Shield(config)
    sb.run(["/path/to/AppRun"])
"""

import ctypes
import ctypes.util
import logging
import os
import shutil
import subprocess
import threading
import tempfile

logger = logging.getLogger(__name__)

# ── Backend enum ───────────────────────────────────────────────────────────

class SandboxBackend:
    SHIELD = "shield"
    FIREJAIL = "firejail"
    BUBBLEWRAP = "bwrap"

# ── Prctl constants ──────────────────────────────────────────────────────

PR_SET_NO_NEW_PRIVS = 38
PR_SET_DUMPABLE = 4
PR_SET_PTRACER = 0x59616d61

PR_SET_PTRACER_DISABLE = 0

# ── mlockall constants ──────────────────────────────────────────────────

MCL_CURRENT = 1

# ── RLIMIT constants ─────────────────────────────────────────────────────

RLIMIT_NPROC = 6
RLIMIT_NOFILE = 7
RLIMIT_FSIZE = 1
RLIMIT_CORE = 4
RLIMIT_STACK = 3

# ── XDG shortcut resolution ────────────────────────────────────────────

XDG_SHORTCUTS = {
    "xdg-download": "~/Downloads",
    "xdg-documents": "~/Documents",
    "xdg-pictures": "~/Pictures",
    "xdg-music": "~/Music",
    "xdg-videos": "~/Videos",
    "xdg-cache": "~/.cache",
    "xdg-config": "~/.config",
    "xdg-data": "~/.local/share",
    "xdg-bin": "~/.local/bin",
    "xdg-statedir": "~/.local/state",
}


def resolve_xdg(path: str) -> str:
    raw = path.rsplit(":", 1)[0] if (path.endswith(":rw") or path.endswith(":ro")) else path
    for shortcut, real in XDG_SHORTCUTS.items():
        if raw == shortcut:
            return real
        if raw.startswith(shortcut + "/"):
            return real + raw[len(shortcut):]
    return path


def _is_under(child: str, parent: str) -> bool:
    child = os.path.realpath(os.path.expanduser(child))
    parent = os.path.realpath(os.path.expanduser(parent))
    if not parent.endswith("/"):
        parent += "/"
    return child == parent.rstrip("/") or child.startswith(parent)


# ── RLIMIT helpers (non-destructive) ──────────────────────────────────────

def _apply_rlimits_ctypes():
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    libc.getrlimit.argtypes = [ctypes.c_int, ctypes.c_void_p]
    libc.setrlimit.argtypes = [ctypes.c_int, ctypes.c_void_p]
    libc.getrlimit.restype = ctypes.c_int
    libc.setrlimit.restype = ctypes.c_int

    class RLimit(ctypes.Structure):
        _fields_ = [("rlim_cur", ctypes.c_ulong), ("rlim_max", ctypes.c_ulong)]

    def _set_if_lower(rlimit, wanted_cur, wanted_max):
        current = RLimit()
        if libc.getrlimit(rlimit, ctypes.byref(current)) != 0:
            return
        new_cur = min(wanted_cur, current.rlim_cur)
        new_max = min(wanted_max, current.rlim_max)
        if new_cur < current.rlim_cur or new_max < current.rlim_max:
            rl = RLimit(new_cur, new_max)
            libc.setrlimit(rlimit, ctypes.byref(rl))

    _set_if_lower(RLIMIT_CORE, 0, 0)
    _set_if_lower(RLIMIT_FSIZE, 268435456, 268435456)
    _set_if_lower(RLIMIT_NOFILE, 8192, 65536)
    _set_if_lower(RLIMIT_NPROC, 65536, 65536)
    _set_if_lower(RLIMIT_STACK, 8388608, 33554432)


# ── Memory hardening (mlockall) ─────────────────────────────────────────

def _apply_memory_hardening():
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        libc.mlockall.argtypes = [ctypes.c_int]
        libc.mlockall.restype = ctypes.c_int
        if libc.mlockall(MCL_CURRENT) != 0:
            logger.debug("mlockall failed: errno=%d", ctypes.get_errno())
    except Exception as e:
        logger.debug("mlockall: %s", e)


# ── Ptrace scope ─────────────────────────────────────────────────────────

def _apply_ptrace_scope():
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                               ctypes.c_ulong, ctypes.c_ulong]
        libc.prctl.restype = ctypes.c_int
        libc.prctl(PR_SET_PTRACER, PR_SET_PTRACER_DISABLE, 0, 0, 0)
    except Exception as e:
        logger.debug("PR_SET_PTRACER: %s", e)


# ── Process hardening (preexec) ────────────────────────────────────────

def _preexec_harden():
    """Apply process hardening in child before exec."""
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                               ctypes.c_ulong, ctypes.c_ulong]
        libc.prctl.restype = ctypes.c_int
        if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            logger.debug("PR_SET_NO_NEW_PRIVS failed: errno=%d", ctypes.get_errno())
        if libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
            logger.debug("PR_SET_DUMPABLE failed: errno=%d", ctypes.get_errno())
    except Exception as e:
        logger.debug("prctl hardening failed: %s", e)

    try:
        _apply_rlimits_ctypes()
    except Exception as e:
        logger.debug("rlimit hardening failed: %s", e)

    try:
        with open("/proc/self/oom_score_adj", "w") as f:
            f.write("-500\n")
    except (OSError, IOError):
        pass

    _apply_memory_hardening()
    _apply_ptrace_scope()


# ── xdg-open daemon ────────────────────────────────────────────────────────

class XdgOpenDaemon:
    """Forwards xdg-open calls from sandboxed app to host via FIFO."""

    def __init__(self):
        self._tmpdir: tempfile.TemporaryDirectory | None = None
        self._fifo_path: str | None = None
        self._wrapper_path: str | None = None
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> str | None:
        try:
            self._tmpdir = tempfile.TemporaryDirectory(prefix="niruvi-xdg-open-")
            wrapper_dir = self._tmpdir.name
            self._fifo_path = os.path.join(wrapper_dir, "fifo")
            self._wrapper_path = os.path.join(wrapper_dir, "xdg-open")
            os.mkfifo(self._fifo_path, 0o600)
            with open(self._wrapper_path, "w") as f:
                f.write("""#!/bin/sh
for arg; do
    printf '%s\\n' "$arg" > "{fifo}"
done
exit 0
""".format(fifo=self._fifo_path))
            os.chmod(self._wrapper_path, 0o755)
            self._running = True
            self._thread = threading.Thread(target=self._listener, daemon=True)
            self._thread.start()
            return wrapper_dir
        except Exception as e:
            logger.warning("Failed to start xdg-open daemon: %s", e)
            self._cleanup()
            return None

    def _listener(self):
        while self._running:
            try:
                with open(self._fifo_path, "r") as fifo:
                    for line in fifo:
                        url = line.strip()
                        if url:
                            try:
                                subprocess.Popen(
                                    ["xdg-open", url],
                                    start_new_session=True,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL,
                                )
                            except Exception as e:
                                logger.debug("xdg-open call failed: %s", e)
            except OSError:
                break
            except Exception as e:
                logger.debug("xdg-open listener error: %s", e)

    def stop(self):
        self._running = False
        self._cleanup()

    def _cleanup(self):
        if self._tmpdir:
            try:
                self._tmpdir.cleanup()
            except OSError:
                pass
            self._tmpdir = None


_xdg_open_daemon: XdgOpenDaemon | None = None
_xdg_open_lock = threading.Lock()


def _get_xdg_open_daemon() -> XdgOpenDaemon | None:
    global _xdg_open_daemon
    if _xdg_open_daemon is None:
        with _xdg_open_lock:
            if _xdg_open_daemon is None:
                d = XdgOpenDaemon()
                if d.start():
                    _xdg_open_daemon = d
                else:
                    return None
    return _xdg_open_daemon


# ── Backend detection ──────────────────────────────────────────────────────

def _which(name: str) -> str | None:
    return shutil.which(name)


def check_firejail_available() -> dict:
    """Check if firejail is installed and usable."""
    result = {
        "available": False,
        "version": None,
        "error": None,
    }
    try:
        r = subprocess.run(
            ["firejail", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            result["available"] = True
            result["version"] = r.stdout.splitlines()[0] if r.stdout else "unknown"
    except FileNotFoundError:
        result["error"] = "firejail not found in PATH"
    except subprocess.TimeoutExpired:
        result["error"] = "firejail --version timed out"
    except Exception as e:
        result["error"] = str(e)
    return result


def check_bwrap_available() -> dict:
    """Check if bubblewrap is installed and usable."""
    result = {
        "available": False,
        "version": None,
        "error": None,
    }
    try:
        r = subprocess.run(
            ["bwrap", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            result["available"] = True
            result["version"] = r.stdout.splitlines()[0] if r.stdout else "unknown"
    except FileNotFoundError:
        result["error"] = "bwrap not found in PATH"
    except subprocess.TimeoutExpired:
        result["error"] = "bwrap --version timed out"
    except Exception as e:
        result["error"] = str(e)
    return result


# ── Config ─────────────────────────────────────────────────────────────────

class ShieldConfig:
    def __init__(
        self,
        enabled: bool = False,
        hardening: bool = True,
        portable_home: bool = False,
        portable_config: bool = False,
        backend: str = SandboxBackend.SHIELD,
    ):
        self.enabled = enabled
        self.hardening = hardening
        self.portable_home = portable_home
        self.portable_config = portable_config
        self.backend = backend

    @classmethod
    def from_dict(cls, d: dict):
        return cls(
            enabled=d.get("enabled", False),
            hardening=d.get("hardening", True),
            portable_home=d.get("portable_home", False),
            portable_config=d.get("portable_config", False),
            backend=d.get("backend", SandboxBackend.SHIELD),
        )

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "hardening": self.hardening,
            "portable_home": self.portable_home,
            "portable_config": self.portable_config,
            "backend": self.backend,
        }


# ── Shield runner ─────────────────────────────────────────────────────────

ALWAYS_KEEP_ENV = {
    "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS", "SESSION_MANAGER",
    "PULSE_SERVER", "PULSE_CLIENTCONFIG", "PULSE_COOKIE", "PULSE_CONFIG",
    "PIPEWIRE_RUNTIME_DIR", "PIPEWIRE_CONFIG_NAME", "PIPEWIRE_LINK",
    "PIPEWIRE_PROTOCOL", "PIPEWIRE_NODE",
    "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_MESSAGES",
    "LC_CTYPE", "LC_NUMERIC", "LC_TIME", "LC_COLLATE", "LC_MONETARY",
    "GTK_MODULES", "GTK_IM_MODULE", "GTK_THEME",
    "QT_QPA_PLATFORM", "QT_WAYLAND_DISABLE_WINDOWDECORATION",
    "QT_QPA_PLATFORMTHEME", "QT_STYLE_OVERRIDE",
    "GDK_BACKEND", "GDK_SCALE", "GDK_DPI_SCALE",
    "CLUTTER_BACKEND", "SDL_VIDEO_DRIVER", "SDL_AUDIODRIVER",
    "EDITOR", "SHELL", "TERM", "COLORTERM",
    "TZ", "NO_AT_BRIDGE", "GTK_DEBUG",
    "JOURNAL_STREAM", "INVOCATION_ID",
}

HARDENED_ENV = {
    "GLIBC_TUNABLES": "glibc.malloc.perturb=0x42:glibc.malloc.tcache_count=0:glibc.malloc.mxfast=0",
    "MALLOC_PERTURB_": "66",
    "MALLOC_CHECK_": "3",
    "LD_BIND_NOW": "1",
}


def _bridge_audio_config(portable_home: str):
    """Bridge PulseAudio/PipeWire/D-Bus config into portable home so IPC works."""
    real_config = os.path.expanduser("~/.config")
    pulse_config = os.path.join(portable_home, ".config", "pulse")
    try:
        os.makedirs(pulse_config, exist_ok=True)
        real_cookie = os.path.join(real_config, "pulse", "cookie")
        portable_cookie = os.path.join(pulse_config, "cookie")
        if os.path.isfile(real_cookie) and not os.path.isfile(portable_cookie):
            os.symlink(real_cookie, portable_cookie)
    except OSError:
        pass
    pw_config = os.path.join(portable_home, ".config", "pipewire")
    try:
        os.makedirs(pw_config, exist_ok=True)
        real_pw = os.path.join(real_config, "pipewire", "client.conf")
        portable_pw = os.path.join(pw_config, "client.conf")
        if os.path.isfile(real_pw) and not os.path.isfile(portable_pw):
            os.symlink(real_pw, portable_pw)
    except OSError:
        pass
    dbus_config = os.path.join(portable_home, ".config", "dbus")
    try:
        os.makedirs(dbus_config, exist_ok=True)
        machine_id = "/etc/machine-id"
        portable_mid = os.path.join(dbus_config, "machine-id")
        if os.path.isfile(machine_id) and not os.path.isfile(portable_mid):
            os.symlink(machine_id, portable_mid)
    except OSError:
        pass


class Shield:
    def __init__(self, config: ShieldConfig):
        self.config = config

    def run(self, cmd: list[str], cwd: str | None = None,
            env: dict | None = None) -> subprocess.Popen | None:
        if not self.config.enabled:
            return self._run_direct(cmd, cwd, env)

        backend = self.config.backend or SandboxBackend.SHIELD

        env = self._build_env(env)
        app_dir = cwd or ""

        # Portable mode env vars
        if self.config.portable_home and app_dir:
            env["HOME"] = os.path.join(app_dir, ".home")
        if self.config.portable_config and app_dir:
            env["XDG_CONFIG_HOME"] = os.path.join(app_dir, ".config")

        # Bridge audio config into portable home
        if self.config.portable_home and app_dir:
            portable_home = os.path.join(app_dir, ".home")
            _bridge_audio_config(portable_home)

        if backend == SandboxBackend.FIREJAIL:
            return self._run_firejail(cmd, cwd, env)
        elif backend == SandboxBackend.BUBBLEWRAP:
            return self._run_bwrap(cmd, cwd, env)
        else:
            return self._run_shield(cmd, cwd, env)

    def _run_shield(self, cmd: list[str], cwd: str | None,
                    env: dict | None) -> subprocess.Popen | None:
        env = env or {}
        app_dir = cwd or ""

        # Inject xdg-open wrapper
        xd = _get_xdg_open_daemon()
        if xd and xd._tmpdir:
            wdir = xd._tmpdir.name
            env["PATH"] = f"{wdir}:" + env.get("PATH", os.environ.get("PATH", ""))
            env["BROWSER"] = os.path.join(wdir, "xdg-open")

        # Inject permission broker
        try:
            from niruvi.core.broker import get_daemon as _get_perm_daemon
            perm_daemon = _get_perm_daemon()
            if perm_daemon and perm_daemon.fifo_dir:
                env["NIRUVI_PERM_BROKER"] = perm_daemon.fifo_dir
        except Exception:
            pass

        preexec_fn = _preexec_harden if self.config.hardening else None

        # Ensure portable dirs exist
        if self.config.portable_home and app_dir:
            os.makedirs(os.path.join(app_dir, ".home"), exist_ok=True)
        if self.config.portable_config and app_dir:
            os.makedirs(os.path.join(app_dir, ".config"), exist_ok=True)

        return subprocess.Popen(
            cmd,
            cwd=cwd or os.getcwd(),
            env=env,
            start_new_session=True,
            preexec_fn=preexec_fn,
        )

    def _run_firejail(self, cmd: list[str], cwd: str | None,
                      env: dict | None) -> subprocess.Popen | None:
        app_dir = cwd or ""
        fj_cmd = ["firejail"]

        if self.config.portable_home and app_dir:
            home_dir = os.path.join(app_dir, ".home")
            os.makedirs(home_dir, exist_ok=True)
            fj_cmd.extend(["--home", home_dir])

        if self.config.portable_config and app_dir:
            cfg_dir = os.path.join(app_dir, ".config")
            os.makedirs(cfg_dir, exist_ok=True)
            fj_cmd.extend(["--private", cfg_dir])

        fj_cmd.append("--x11")

        if not self.config.hardening:
            fj_cmd.append("--noroot")
        fj_cmd.append("--")

        fj_cmd.extend(cmd)

        return subprocess.Popen(
            fj_cmd,
            cwd=cwd or os.getcwd(),
            env=env,
            start_new_session=True,
        )

    def _run_bwrap(self, cmd: list[str], cwd: str | None,
                   env: dict | None) -> subprocess.Popen | None:
        app_dir = cwd or ""
        bwrap_cmd = [
            "bwrap",
            "--unshare-all",
            "--new-session",
            "--die-with-parent",
        ]

        bwrap_cmd.extend(["--proc", "/proc"])
        bwrap_cmd.extend(["--dev", "/dev"])
        bwrap_cmd.extend(["--ro-bind", "/", "/"])
        bwrap_cmd.extend(["--tmpfs", "/tmp"])
        bwrap_cmd.extend(["--tmpfs", "/var/tmp"])
        bwrap_cmd.extend(["--tmpfs", "/dev/shm"])

        if self.config.portable_home and app_dir:
            home_dir = os.path.join(app_dir, ".home")
            os.makedirs(home_dir, exist_ok=True)
            bwrap_cmd.extend(["--bind", home_dir, os.path.expanduser("~")])

        if self.config.portable_config and app_dir:
            cfg_dir = os.path.join(app_dir, ".config")
            os.makedirs(cfg_dir, exist_ok=True)
            bwrap_cmd.extend(["--bind", cfg_dir, os.path.expanduser("~/.config")])

        bwrap_cmd.append("--share-net")

        bwrap_cmd.append("--")
        bwrap_cmd.extend(cmd)

        return subprocess.Popen(
            bwrap_cmd,
            cwd=cwd or os.getcwd(),
            env=env,
            start_new_session=True,
        )

    def _build_env(self, base_env: dict | None) -> dict:
        env = (base_env or os.environ).copy()
        for k in ALWAYS_KEEP_ENV:
            if k in os.environ:
                env[k] = os.environ[k]
        if self.config.hardening:
            for k, v in HARDENED_ENV.items():
                if k not in env:
                    env[k] = v
        return env

    def _run_direct(self, cmd: list[str], cwd: str | None,
                    env: dict | None) -> subprocess.Popen:
        result_env = (env or os.environ).copy()
        for k in ALWAYS_KEEP_ENV:
            if k in os.environ:
                result_env[k] = os.environ[k]
        return subprocess.Popen(
            cmd, cwd=cwd or os.getcwd(),
            env=result_env,
            start_new_session=True,
        )


def check_shield_available() -> dict:
    info = {
        "hardening": True,
        "portable_mode": True,
        "xdg_open_daemon": True,
        "memory_locking": True,
        "ptrace_scope": True,
        "malloc_hardening": True,
        "backends": {"shield": True},
    }
    fj = check_firejail_available()
    info["backends"]["firejail"] = fj["available"]
    info["firejail_version"] = fj["version"]
    bw = check_bwrap_available()
    info["backends"]["bwrap"] = bw["available"]
    info["bwrap_version"] = bw["version"]
    return info
