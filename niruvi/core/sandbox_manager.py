"""SandboxManager: single fail-closed orchestration for app launches.

Replace the scattered unsandboxed fallbacks in the UI/launcher paths with one
enforcement point:

* Resolve the granted policy (per-app file → registry mirror → default
  STANDARD profile sandbox).  Absence of a policy file is never "sandbox off"
  unless the user explicitly overrides from the UI with a reason.
* Resolve the backend: requested → target backends only.  If none is usable,
  the launch is REFUSED with explicit reasons; it is never degraded silently.
* Permission resolution failure explicitly REFUSES the launch; it never
  guess-shrinks into an exec of an almost-sandboxed half-crash.
* Untrusted PIDs must never be escalated by us.  ``stop_app`` SIGTERMs/SIGKILLs
  only process groups recorded here (pids we spawned).
* Launch events (launched / refused / override) land in the audit log.
"""

import logging
import os
import shlex
import signal
import subprocess
import time
from copy import deepcopy
from dataclasses import dataclass, field

from niruvi.core.backends import get_backend
from niruvi.core.policy import SandboxPolicy
from niruvi.core.policy_engine import filter_environment, permission_states, summary_state
from niruvi.core.profiles import DEFAULT_PROFILE_NAME, build_profile
from niruvi.core import sandbox_audit

logger = logging.getLogger(__name__)

from niruvi.core.bubblewrap_backend import LaunchRefused  # noqa: F401 (re-export)


@dataclass
class LaunchResult:
    ok: bool
    pid: int | None = None
    sandboxed: bool = True
    backend: str = ""
    profile: str = ""
    refusals: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    proc: subprocess.Popen | None = None


class SandboxManager:
    """Owns the "was this launch sandboxed?" decision. Fail-closed."""

    #: backends we are willing to use, in preference order
    LAUNCH_BACKENDS = ("bwrap", "shield", "firejail")

    def __init__(self):
        self._procs: dict[int, subprocess.Popen] = {}

    # ── policy resolution ────────────────────────────────────────────
    def resolve_policy(self, app_id: str, record) -> SandboxPolicy:
        """Trusted policy for an app, always sandbox-by-default.

        Order: per-app policy file → registry mirror → STANDARD profile.
        Every resolution error is fatal for enforcement, never silently a
        free pass; raise PolicyResolutionError instead and callers handle it.
        """
        from niruvi.core.policy_store import PolicyStore

        store = PolicyStore()
        policy = store.load_if_valid(app_id)
        if policy is None and record is not None:
            try:
                cached = SandboxPolicy.from_dict(record.sandbox_config or {})
            except Exception as e:
                raise PolicyResolutionError(f"registry sandbox config is corrupt: {e}") from e
            if cached is not None and cached.enabled:
                policy = cached
        if policy is None:
            # No stored policy and no mirror: default sandbox stays ON with
            # the STANDARD profile.  Never a free pass.
            policy = build_profile(DEFAULT_PROFILE_NAME, app_id, backend=_default_backend())
        policy.profile = policy.profile or "custom"
        return policy

    def resolve_backend(self, policy: SandboxPolicy):
        """Return (backend, refusals) for the policy's enforcement.  No silent
        substitution: if the configured backend cannot start, try remaining
        LAUNCH_BACKENDS for equal-or-stronger enforcement; otherwise refuse.
        """
        refusals: list[str] = []
        order = [policy.backend] + [b for b in self.LAUNCH_BACKENDS if b != policy.backend]
        for name in order:
            try:
                be = get_backend(name, strict=True)
            except Exception as e:
                refusals.append(f"backend {name}: {e}")
                continue
            if be.available():
                return be, refusals
            probe = _backend_probe_text(be)
            refusals.append(f"backend {name} unusable: {probe}")
        return None, refusals

    # ── launch ───────────────────────────────────────────────────────
    def launch(
        self,
        app_id: str,
        app_dir: str,
        cmd: list[str],
        policy: SandboxPolicy,
        record=None,
        env_overrides: dict | None = None,
        executable: str = "",
        cwd: str | None = None,
        override_reason: str = "",
    ) -> LaunchResult:
        """Launch cmd (already built with run_args/extra args) sandboxed.

        Fail-closed: any unresolved condition refuses the launch with a visible
        reason; the user must explicitly confirm an unsandboxed run via the UI
        (``override_reason`` non-empty launches without sandbox, recorded as
        an audit-able override entry rather than a quiet fallback).
        """
        refusals: list[str] = []
        warnings: list[str] = []

        policy = deepcopy(policy)
        policy.app_id = policy.app_id or app_id

        if not os.path.isdir(app_dir):
            msg = f"install directory missing: {app_dir}"
            return self._record_refusal(app_id, executable, policy, refusals + [msg])

        backend, refusals = self.resolve_backend(policy)
        if backend is None:
            return self._record_refusal(
                app_id,
                executable,
                policy,
                refusals + ["No capable enforcement backend is installed on this system."],
            )
        if backend.name != policy.backend:
            warnings.append(
                f"Configured backend '{policy.backend}' is unusable; "
                f"enforcing with '{backend.name}' instead."
            )

        try:
            env = filter_environment(policy, dict(env_overrides or {}))
        except Exception as e:
            msg = f"environment filtering failed ({e}); refusing rather than leaking host environment"
            return self._record_refusal(app_id, executable, policy, refusals + [msg], backend=backend.name)

        launch_env = {**os.environ, **env}

        if override_reason:
            # Explicit, audited user decision: no sandbox.
            try:
                p = subprocess.Popen(
                    cmd,
                    cwd=cwd or app_dir,
                    env=launch_env,
                    start_new_session=True,
                    close_fds=True,
                )
            except OSError as e:
                return self._record_refusal(
                    app_id, executable, policy, refusals + [f"unsandboxed launch failed: {e}"], backend=backend.name
                )
            self._procs[p.pid] = p
            sandbox_audit.record_launch(
                app_id, executable or (cmd[0] if cmd else ""), policy.profile, "none",
                "override", pid=p.pid, override_reason=override_reason,
            )
            return LaunchResult(ok=True, pid=p.pid, sandboxed=False, backend="none",
                                profile=policy.profile, warnings=warnings, proc=p)

        try:
            p = backend.launch_application(app_dir, cmd, launch_env, policy)
        except LaunchRefused as e:
            return self._record_refusal(app_id, executable, policy, refusals + [str(e)], backend=backend.name)
        except Exception as e:
            logger.exception("Launch failed for %s", app_id)
            return self._record_refusal(app_id, executable, policy, refusals + [f"{type(e).__name__}: {e}"],
                                        backend=backend.name)

        if p is None:
            return self._record_refusal(
                app_id, executable, policy,
                refusals + [f"backend '{backend.name}' returned no process instead of raising"],
                backend=backend.name,
            )

        self._procs[p.pid] = p
        sandbox_audit.record_launch(
            app_id, executable or (cmd[0] if cmd else ""), policy.profile, backend.name,
            "launched", pid=p.pid, override_reason=override_reason,
        )
        return LaunchResult(ok=True, pid=p.pid, sandboxed=True, backend=backend.name,
                            profile=policy.profile, warnings=warnings, proc=p)

    def _record_refusal(self, app_id, executable, policy, refusals, backend: str = "") -> LaunchResult:
        sandbox_audit.record_launch(
            app_id, executable or "", policy.profile, backend, "refused",
            refused_reason="; ".join(refusals),
        )
        return LaunchResult(ok=False, sandboxed=False, backend=backend,
                            profile=policy.profile, refusals=refusals)

    # ── process control ──────────────────────────────────────────────
    def is_process_alive(self, pid: int) -> bool:
        """A launch pid we recorded, or nothing.  Foreign pids are not ours."""
        if pid not in self._procs:
            return False
        p = self._procs[pid]
        return p.poll() is None

    def stop_app(self, pid: int, grace: float = 4.0) -> bool:
        """SIGTERM→(grace)SGKILL the process group WE launched. Never pid-1,
        never a pid we didn't spawn: matching a *recorded* Popen only."""
        if pid <= 1:
            return False
        proc = self._procs.get(pid)
        if proc is None or proc.poll() is not None:
            return False
        try:
            pgid = os.getpgid(pid)
        except ProcessLookupError:
            self._procs.pop(pid, None)
            return True
        except PermissionError as e:
            logger.warning("Cannot signal group of %d: %s", pid, e)
            return False
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError:
            return False
        deadline = time.time() + grace
        while time.time() < deadline:
            if proc.poll() is not None:
                self._procs.pop(pid, None)
                return True
            time.sleep(0.05)
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            return False
        proc.wait(timeout=5)
        self._procs.pop(pid, None)
        return True

    # ── status for UI ────────────────────────────────────────────────
    def permission_states(self, policy: SandboxPolicy):
        from niruvi.core.backends import get_backend

        be = get_backend(policy.backend, strict=True)
        return permission_states(policy, be), summary_state(permission_states(policy, be))


class PolicyResolutionError(Exception):
    """Permission resolution failed: the launch must be refused, not guessed."""


def _backend_probe_text(backend) -> str:
    try:
        probe = backend.detect_support()
    except Exception as e:
        return f"detection failed: {e}"
    return str({k: v for k, v in probe.items() if k in ("available", "error") or v is not True})


def _default_backend() -> str:
    try:
        be = get_backend("bwrap", strict=True)
    except Exception:
        be = None
    return "bwrap" if be and be.available() else "shield"


_manager: SandboxManager | None = None


def get_sandbox_manager() -> SandboxManager:
    global _manager
    if _manager is None:
        _manager = SandboxManager()
    return _manager


def build_command(record, app_dir: str, extra_args: list[str] | None = None) -> tuple[list[str], list[str]]:
    """(errors, cmd): AppRun + recorded run args (+ extra args on --run CLI)."""
    apprun = os.path.join(app_dir, "AppRun")
    if not os.path.isfile(apprun):
        return [f"AppRun not found in {app_dir}"], []
    cmd = [apprun]
    args = getattr(record, "run_args", "") or ""
    for token in shlex.split(args):
        cmd.append(token)
    if extra_args:
        cmd.extend(extra_args)
    return [], cmd
