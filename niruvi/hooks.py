import logging
import os
import stat
import subprocess

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
        for f in sorted(os.listdir(base)):
            path = os.path.join(base, f)
            if f.endswith(".hook") and os.path.isfile(path) and os.access(path, os.X_OK):
                if f not in seen:
                    seen.add(f)
                    hooks.append(path)
    return hooks


def run_hooks(app_name: str, app_dir: str, env: dict | None = None) -> list[dict]:
    results = []
    for hook_path in list_hooks(app_name):
        try:
            hook_env = os.environ.copy()
            hook_env["APP_NAME"] = app_name
            hook_env["APP_DIR"] = app_dir
            if env:
                hook_env.update(env)
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
                logging.info("Hook %s stdout: %s", hook_path, out)
            if err:
                logging.warning("Hook %s stderr: %s", hook_path, err)
            results.append({
                "hook": hook_path,
                "returncode": result.returncode,
                "stdout": out,
                "stderr": err,
            })
        except subprocess.TimeoutExpired:
            logging.warning("Hook %s timed out", hook_path)
            results.append({"hook": hook_path, "returncode": -1, "stdout": "", "stderr": "timed out"})
        except OSError as e:
            logging.warning("Failed to run hook %s: %s", hook_path, e)
            results.append({"hook": hook_path, "returncode": -1, "stdout": "", "stderr": str(e)})
    return results


def write_hook(app_name: str, hook_name: str, content: str) -> str:
    d = ensure_hooks_dir(app_name)
    path = os.path.join(d, hook_name)
    if not hook_name.endswith(".hook"):
        path += ".hook"
    with open(path, "w") as f:
        f.write(content)
    os.chmod(path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    return path


def remove_hook(app_name: str, hook_name: str) -> bool:
    path = os.path.join(get_app_hooks_dir(app_name), hook_name)
    if not os.path.isfile(path):
        path = os.path.join(HOOKS_DIR, hook_name)
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False
