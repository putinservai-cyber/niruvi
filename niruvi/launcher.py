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

    # SandboxManager owns the fail-closed decision; a quiet unsandboxed
    # fallback is not an option from the CLI either: if the sandbox cannot
    # be promised, the caller must know.
    from niruvi.core.sandbox_manager import get_sandbox_manager, PolicyResolutionError

    sandbox = get_sandbox_manager()
    try:
        policy = sandbox.resolve_policy(app_name, record)
    except PolicyResolutionError as e:
        print(f"Launch refused for {app_name}: {e}", file=sys.stderr)
        return 3

    # Only trusted per-app env vars are passed along; the sandbox engine
    # filters the host environment through its compiled allowlist.
    env = dict(record.env_vars) if record.env_vars else {}

    cmd = [apprun]
    if record.run_args:
        cmd.extend(shlex.split(record.run_args))
    if extra_args:
        cmd.extend(extra_args)

    if "--unsandboxed" in sys.argv:
        rc = sandbox.launch(
            app_name, app_dir, cmd, policy, record=record,
            env_overrides=env, executable=apprun, cwd=app_dir,
            override_reason="explicit --unsandboxed CLI override",
        )
        if not rc.ok:
            print("Unsandboxed launch failed: " + "; ".join(rc.refusals), file=sys.stderr)
            return 2
        if policy.timeout and policy.timeout > 0:
            rc.proc.wait()
        return 0

    rc = sandbox.launch(
        app_name, app_dir, cmd, policy, record=record,
        env_overrides=env, executable=apprun, cwd=app_dir,
    )
    if not rc.ok:
        print(f"Launch refused for {app_name}:", file=sys.stderr)
        for r in rc.refusals:
            print(f"  - {r}", file=sys.stderr)
        print("Use --unsandboxed to run without the sandbox (audited).", file=sys.stderr)
        return 2
    if not rc.sandboxed:
        print(f"Warning: {app_name} is RUNNING WITHOUT THE SANDBOX ({rc.backend}).", file=sys.stderr)
    for w in rc.warnings:
        print(f"Warning: {w}", file=sys.stderr)
    if policy.timeout and policy.timeout > 0:
        rc.proc.wait()
    return 0
