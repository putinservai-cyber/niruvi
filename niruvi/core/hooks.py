import logging
import os
import stat
import subprocess

logger = logging.getLogger(__name__)

HOOKS_DIR = os.path.expanduser("~/.config/niruvi/hooks")


def get_app_hooks_dir(app_name: str) -> str:
    return os.path.join(HOOKS_DIR, app_name)


def get_global_hooks_dir() -> str:
    return HOOKS_DIR


def ensure_hooks_dir(app_name: str = "") -> str:
    d = get_app_hooks_dir(app_name) if app_name else HOOKS_DIR
    os.makedirs(d, exist_ok=True)
    return d


def list_hooks(app_name: str) -> list[str]:
    hooks = []
    seen = set()
    for base in [get_global_hooks_dir(), get_app_hooks_dir(app_name)]:
        if not os.path.isdir(base):
            continue
        real_base = os.path.realpath(base)
        for f in sorted(os.listdir(base)):
            path = os.path.join(base, f)
            real_path = os.path.realpath(path)
            if not real_path.startswith(real_base + os.sep):
                logger.warning("Hook %s escapes hooks directory — skipping", path)
                continue
            if f.endswith(".hook") and os.path.isfile(path) and f not in seen:
                seen.add(f)
                hooks.append(path)
    return hooks


def _check_hook_secure(hook_path: str) -> bool:
    try:
        st = os.stat(hook_path)
        if st.st_uid != os.getuid():
            logger.warning("Hook %s is not owned by current user — skipping", hook_path)
            return False
        if st.st_mode & stat.S_IWOTH:
            logger.warning("Hook %s is world-writable — skipping", hook_path)
            return False
        if st.st_mode & stat.S_IWGRP:
            parent_st = os.stat(os.path.dirname(hook_path))
            if parent_st.st_gid != st.st_gid:
                logger.warning("Hook %s is group-writable by a different group — skipping", hook_path)
                return False
        return True
    except OSError as e:
        logger.warning("Cannot stat hook %s: %s — skipping", hook_path, e)
        return False


def _minimal_env(extra: dict | None = None) -> dict:
    """Build a minimal safe environment for hook scripts."""
    safe = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": os.environ.get("HOME", "/tmp"),
        "USER": os.environ.get("USER", ""),
        "LANG": os.environ.get("LANG", "C"),
        "SHELL": "/bin/sh",
        "TERM": os.environ.get("TERM", "dumb"),
    }
    if extra:
        safe.update(extra)
    return safe


def run_hooks(app_name: str, app_dir: str, env: dict | None = None) -> list[dict]:
    results = []
    for hook_path in list_hooks(app_name):
        if not _check_hook_secure(hook_path):
            results.append(
                {
                    "hook": hook_path,
                    "returncode": -1,
                    "stdout": "",
                    "stderr": "skipped: insecure permissions or ownership",
                }
            )
            continue
        try:
            hook_env = _minimal_env({"APP_NAME": app_name, "APP_DIR": app_dir})
            if env:
                hook_env.update(
                    {
                        k: v
                        for k, v in env.items()
                        if k in ("DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR", "WAYLAND_DISPLAY")
                    }
                )
            result = subprocess.run(
                [hook_path],
                capture_output=True,
                text=True,
                timeout=30,
                env=hook_env,
            )
            out = result.stdout.strip()
            err = result.stderr.strip()
            if out:
                logger.info("Hook %s stdout: %s", hook_path, out)
            if err:
                logger.warning("Hook %s stderr: %s", hook_path, err)
            results.append(
                {
                    "hook": hook_path,
                    "returncode": result.returncode,
                    "stdout": out,
                    "stderr": err,
                }
            )
        except subprocess.TimeoutExpired:
            logger.warning("Hook %s timed out", hook_path)
            results.append({"hook": hook_path, "returncode": -1, "stdout": "", "stderr": "timed out"})
        except OSError as e:
            logger.warning("Failed to run hook %s: %s", hook_path, e)
            results.append({"hook": hook_path, "returncode": -1, "stdout": "", "stderr": str(e)})
    return results


def write_hook(app_name: str, hook_name: str, content: str) -> str:
    d = ensure_hooks_dir(app_name)
    path = os.path.join(d, hook_name)
    if not hook_name.endswith(".hook"):
        path += ".hook"
    with open(path, "w") as f:
        f.write(content)
    os.chmod(path, stat.S_IRWXU)
    return path


def remove_hook(app_name: str, hook_name: str) -> bool:
    candidates = [
        os.path.join(get_app_hooks_dir(app_name), hook_name),
        os.path.join(get_app_hooks_dir(app_name), hook_name + ".hook"),
        os.path.join(HOOKS_DIR, hook_name),
        os.path.join(HOOKS_DIR, hook_name + ".hook"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            os.remove(path)
            return True
    return False
