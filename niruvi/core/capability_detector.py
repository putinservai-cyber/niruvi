"""CapabilityDetector — one cached, accurate capability report.

Detection shells out (``bwrap --version``, ``unshare`` probes) and pokes the
kernel (Landlock ABI), so it must never run per widget refresh or per idle
tick.  The detector builds a report once and caches it; call
:func:`invalidate` after installing/removing a backend or changing relevant
settings so the next :func:`report` re-probes synchronously (or via a worker
thread from the UI).

The report is honest: unsupported items carry a reason, never a guess.
"""

import functools
import logging
import os
import platform
import re
import subprocess

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def _cached_report() -> tuple:
    return (_build_report(),)  # tuple-of-one so lru_cache can store the dict


def invalidate() -> None:
    """Drop the cached report (e.g. after the user installs bubblewrap)."""
    _cached_report.cache_clear()


def report(refresh: bool = False) -> dict:
    """The cached capability report; ``refresh=True`` re-probes."""
    if refresh:
        invalidate()
    return _cached_report()[0]


def _kernel_version() -> str:
    return platform.release()


def _kernel_ge(minimum: tuple) -> bool | None:
    try:
        release = platform.release()
        parts = re.match(r"(\d+)\.(\d+)", release)
        if not parts:
            return None
        actual = (int(parts.group(1)), int(parts.group(2)))
        return actual >= minimum
    except Exception:
        return None


def _kernel_config_enabled(option: str) -> bool | None:
    """Read CONFIG_*=y from /boot or /proc/config.gz when readable.

    Returns None when unknown (config not readable on many distros).
    """
    try:
        if os.path.exists("/proc/config.gz"):
            import gzip

            with gzip.open("/proc/config.gz", "rt") as f:
                for line in f:
                    if line.startswith(option + "=") and line.strip().endswith("=y"):
                        return True
            return False
        for root in ("/boot", "/lib/modules"):
            if not os.path.isdir(root):
                continue
            base = _kernel_version()
            for dirpath, _dirs, files in os.walk(root):
                if os.path.basename(dirpath) != base or "config" not in " ".join(files):
                    continue
                for name in files:
                    if name.startswith("config-") or name == "config":
                        path = os.path.join(dirpath, name)
                        if os.access(path, os.R_OK):
                            with open(path) as f:
                                for line in f:
                                    if line.startswith(option + "=") and line.strip().endswith("=y"):
                                        return True
                            return False
    except OSError:
        return None
    return None


def _probe_unshare_flag(flag: list[str]) -> dict:
    """Try `unshare <flag> true` — direct exec, no shell."""
    try:
        r = subprocess.run(
            ["unshare", *flag, "true"],
            capture_output=True,
            timeout=5,
        )
        ok = r.returncode == 0
        detail = "" if ok else (r.stderr.decode(errors="replace").strip().splitlines() or [""])[-1]
        return {"available": ok, "detail": detail}
    except FileNotFoundError:
        return {"available": False, "detail": "unshare not found in PATH"}
    except subprocess.TimeoutExpired:
        return {"available": False, "detail": "unshare probe timed out"}
    except Exception as e:
        return {"available": False, "detail": str(e)}


def _probe_userns() -> dict:
    # Modern kernels gate this on kernel.unprivileged_userns_clone sysctl.
    try:
        with open("/proc/sys/kernel/unprivileged_userns_clone") as f:
            if int(f.read().strip()) == 0:
                return {"available": False, "detail": "unprivileged_userns_clone=0 (disabled by sysctl)"}
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        pass
    result = _probe_unshare_flag(["--user"])
    if not result["available"]:
        try:
            max_net = int(open("/proc/sys/user/max_user_namespaces").read())
            if max_net == 0:
                result["detail"] = "user.max_user_namespaces=0 (disabled by sysctl)"
        except (OSError, ValueError):
            pass
    return result


def _probe_pid_ns() -> dict:
    return _probe_unshare_flag(["--user", "--pid", "--fork"])


def _probe_ipc_ns() -> dict:
    return _probe_unshare_flag(["--user", "--ipc"])


def _probe_net_ns() -> dict:
    return _probe_unshare_flag(["--user", "--net"])


def _probe_bwrap() -> dict:
    try:
        r = subprocess.run(
            ["bwrap", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0:
            version = (r.stdout.splitlines() or [""])[0].strip()
            m = re.match(r"(\d+)\.(\d+)", version)
            minimum = (0, 5)
            if m and (int(m.group(1)), int(m.group(2))) < minimum:
                return {
                    "available": False,
                    "version": version,
                    "error": f"bubblewrap {version} is too old (need >= {'.'.join(map(str, minimum))})",
                }
            return {"available": True, "version": version, "error": None}
        return {"available": False, "version": None, "error": f"bwrap --version exited {r.returncode}"}
    except FileNotFoundError:
        return {"available": False, "version": None, "error": "bwrap not found in PATH"}
    except subprocess.TimeoutExpired:
        return {"available": False, "version": None, "error": "bwrap --version timed out"}
    except Exception as e:
        return {"available": False, "version": None, "error": str(e)}


def _probe_firejail() -> dict:
    try:
        r = subprocess.run(
            ["firejail", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0:
            return {"available": True, "version": (r.stdout.splitlines() or [""])[0], "error": None}
        return {"available": False, "version": None, "error": f"firejail --version exited {r.returncode}"}
    except FileNotFoundError:
        return {"available": False, "version": None, "error": "firejail not found in PATH"}
    except subprocess.TimeoutExpired:
        return {"available": False, "version": None, "error": "firejail --version timed out"}
    except Exception as e:
        return {"available": False, "version": None, "error": str(e)}


def _probe_landlock() -> dict:
    try:
        from niruvi.core.sandbox import landlock_supported

        ok = landlock_supported()
        return {
            "available": ok,
            "detail": "" if ok else "Kernel does not expose Landlock (Linux 5.13+ ABI required).",
        }
    except Exception as e:
        return {"available": False, "detail": str(e)}


def _probe_seccomp() -> dict:
    # seccomp filters are nearly always present; verify via Kconfig when
    # readable, otherwise assume present (Linux >= 3.5 default).
    cfg = _kernel_config_enabled("CONFIG_SECCOMP_FILTER")
    if cfg is None:
        return {"available": None, "detail": "Syscall filtering available (kernel config not readable to confirm)."}
    if cfg:
        return {"available": True, "detail": ""}
    return {"available": False, "detail": "CONFIG_SECCOMP_FILTER is not enabled in this kernel."}


def _build_report() -> dict:
    userns = _probe_userns()
    net_ns = _probe_net_ns()
    pid_ns = _probe_pid_ns()
    ipc_ns = _probe_ipc_ns()
    bwrap = _probe_bwrap()
    landlock = _probe_landlock()
    seccomp = _probe_seccomp()
    firejail = _probe_firejail()

    summary = {
        "bubblewrap_usable": bool(bwrap["available"] and net_ns["available"] and userns["available"]),
        "protected_possible": bool(bwrap["available"] and net_ns["available"]),
    }
    return {
        "kernel": _kernel_version(),
        "namespaces": {
            "user": userns,
            "pid": pid_ns,
            "ipc": ipc_ns,
            "network": net_ns,
        },
        "bubblewrap": bwrap,
        "firejail": firejail,
        "landlock": {
            "available": landlock["available"],
            "detail": landlock["detail"],
        },
        "seccomp": {
            "available": seccomp["available"],
            "detail": seccomp["detail"],
        },
        "summary": summary,
    }
