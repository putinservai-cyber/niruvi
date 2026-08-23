"""Headless app launcher used by `niruvi --run` and desktop entries.

Desktop entries normally point straight at an app's AppRun, which
bypasses the sandbox. When an app has sandboxing enabled, its desktop
entry instead runs `niruvi --run <app>` so the configured Shield /
firejail / bwrap sandbox is applied on every launch, not only from
inside the Niruvi GUI.
"""

import logging
import os
import shlex
import shutil
import subprocess
import sys

logger = logging.getLogger(__name__)


def _find_launcher() -> str | None:
    """Return the niruvi launcher executable (or None if unavailable)."""
    try:
        which = shutil.which("niruvi")
        if which:
            return which
    except Exception:
        pass
    from niruvi.config import INSTALLED_DIR

    apprun = os.path.join(INSTALLED_DIR, "AppRun")
    if os.path.isfile(apprun) and os.access(apprun, os.X_OK):
        return apprun
    return None


def build_launch_command(app_name: str, extra_args: list[str] | None = None) -> list[str] | None:
    """Build a command that launches `app_name` through Niruvi's sandbox-aware
    launcher, forwarding any extra arguments. Returns None if no launcher
    exists on this system."""
    launcher = _find_launcher()
    if not launcher:
        return None
    cmd = [launcher, "--run", app_name]
    if extra_args:
        cmd.append("--")
        cmd.extend(extra_args)
    return cmd


def launcher_exec_line(app_name: str, fields: str = "%F") -> str:
    """Desktop-file Exec= value that routes through the sandbox-aware launcher."""
    launcher = _find_launcher()
    if not launcher:
        return ""
    quoted = shlex.quote(launcher)
    parts = [quoted, "--run", shlex.quote(app_name)]
    if fields:
        parts.append(fields)
    return " ".join(parts)


def run_app_headless(app_name: str, extra_args: list[str] | None = None) -> int:
    """Launch an installed app headlessly with its configured sandbox.

    Returns 0 on successful spawn (the app keeps running detached) or a
    non-zero exit code on error.
    """
    from niruvi.config import load_settings

    load_settings()

    from niruvi.desktop.installation_registry import InstallationRegistry

    registry = InstallationRegistry()
    record = registry.get(app_name) or registry.lookup_by_name(app_name)
    if not record:
        print(f"Error: '{app_name}' is not installed.", file=sys.stderr)
        return 1

    app_dir = record.path
    if not app_dir or not os.path.isdir(app_dir):
        print(f"Error: install directory not found for '{app_name}': {app_dir}", file=sys.stderr)
        return 1

    apprun = os.path.join(app_dir, "AppRun")
    if not os.path.isfile(apprun) or not os.access(apprun, os.X_OK):
        print(f"Error: AppRun not found or not executable in {app_dir}", file=sys.stderr)
        return 1

    # Only trusted per-app overrides — Shield._build_env() filters the host
    # environment through its security allowlist; the unsandboxed path below
    # re-inherits the full host environment.
    env = dict(record.env_vars) if record.env_vars else {}

    sc = record.sandbox_config or {}
    if sc.get("portable_home") or sc.get("portable", False):
        env["HOME"] = os.path.join(app_dir, ".home")
    if sc.get("portable_config"):
        env["XDG_CONFIG_HOME"] = os.path.join(app_dir, ".config")

    cmd = [apprun]
    if record.run_args:
        cmd.extend(shlex.split(record.run_args))
    if extra_args:
        cmd.extend(extra_args)

    unsandboxed = "--unsandboxed" in sys.argv

    if not unsandboxed and sc.get("enabled", False):
        from niruvi.core.sandbox import Shield, ShieldConfig

        try:
            config = ShieldConfig.from_dict(sc)
        except Exception:
            config = ShieldConfig(enabled=True)
        sb = Shield(config)
        p = sb.run(cmd, cwd=app_dir, env=env)
        if p is not None:
            if config.timeout and config.timeout > 0:
                # Stay alive so the Shield watchdog can enforce the timeout.
                p.wait()
            return 0
        print("Warning: sandbox failed to start, launching unsandboxed.", file=sys.stderr)

    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    subprocess.Popen(cmd, cwd=app_dir, env=full_env, start_new_session=True)
    return 0
