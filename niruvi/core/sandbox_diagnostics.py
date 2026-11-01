"""Sandbox diagnostics: REAL verification probes inside a running plan.

A state shown in the UI as "Verified" must come from executing actual probes
inside the sandbox (not from configuration alone).  Every probe here runs
direct bwrap/plan argv without a shell for the inspected command; the few
compound checks use a controlled /bin/sh -c string with no interpolation of
user data.

Verification is honest about its limits: it reports what was measured (e.g.
network visibility, device node absence) and, when the sandbox cannot run a
probe (backend refuses, bwrap missing), it reports None rather than
decorating unknowns as verified.
"""

import logging
import subprocess

from niruvi.core.bubblewrap_backend import (
    BubblewrapSandbox,
    LaunchRefused,
    build_bwrap_sandbox,
)
from niruvi.core.policy import SandboxPolicy

logger = logging.getLogger(__name__)

# /bin/sh -c probes with no user data interpolated.
_PROBE_NET_HIDDEN = ["/bin/sh", "-c", "test $(ls /sys/class/net 2>/dev/null | wc -l) -eq 0"]
# coreutils' real `/usr/bin/[` binary (the shell builtin `test` cannot be
# exec'd from bwrap; `[` is the regular-file spelling of the same test).
_MISSING = ["/usr/bin/["]


def _absent_probe(path: str) -> list:
    # `[` requires a trailing `]`: /usr/bin/[ ! -e PATH .
    return [*_MISSING, "!", "-e", path, "]"]




def _run_sbprobe(sandbox: BubblewrapSandbox, argv: list[str], timeout: float = 30):
    """Run a probe command.  Returns (verified: bool|None, detail: str)."""
    try:
        cp = sandbox.run_probe(argv, timeout=timeout)
    except (LaunchRefused, FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.debug("probe failed to run: %s", e)
        return None, f"probe could not run: {e}"
    if cp.returncode == 0:
        return True, ""
    stderr = (cp.stderr or b"").decode(errors="replace").strip()
    return False, stderr or f"probe exited {cp.returncode}"


def verify_sandbox(policy: SandboxPolicy, app_dir: str, env: dict | None = None) -> dict:
    """Run real probes against a plan derived from the policy.

    Returns {"backend": str, "verified": bool|None, "checks": {...},
    "summary": "verified"|"partial"|"not-verified", "detail": [str,...]}.
    """
    checks: dict[str, object] = {"backend": "bwrap", "verified": None}
    try:
        sb = build_bwrap_sandbox(policy, app_dir, env or {})
    except LaunchRefused as e:
        return _not_verified(str(e))
    checks["backend"] = "bwrap"

    details: list[str] = []

    smoke_ok, smoke_err = _run_sbprobe(sb, ["true"])
    checks["smoke"] = smoke_ok
    if smoke_ok is False:
        details.append("sandbox cannot even exec /bin/true: " + smoke_err)
        return {"backend": "bwrap", "verified": False, "checks": checks,
                "summary": "not-verified", "detail": details}
    if smoke_ok is None:
        return {"backend": "bwrap", "verified": None, "checks": checks,
                "summary": "not-verified", "detail": ["probe execution unavailable"]}

    devices_flags = policy.devices or {}
    for label, want_blocked, probe in (
        ("microphone", not devices_flags.get("microphone"), _absent_probe("/dev/snd")),
        ("camera", not devices_flags.get("camera"), _absent_probe("/dev/video0")),
        ("gpu", not devices_flags.get("gpu"), _absent_probe("/dev/dri")),
        ("usb", not devices_flags.get("usb"), _absent_probe("/dev/bus/usb")),
        ("input", not devices_flags.get("input"), _absent_probe("/dev/input/event0")),
    ):
        ok, err = _run_sbprobe(sb, probe)
        # verified ONLY when the policy intends blocked AND we probed the node
        # absent; outcome is None when it's purely a configured-on device.
        if want_blocked and ok is True:
            checks[label] = "verified"
        elif want_blocked and ok is False and label == "microphone" and devices_flags.get("audio"):
            # Same ALSA node as audio output: capture cannot be split from
            # output while sound is allowed (documented limitation).
            checks[label] = "limited"
        elif want_blocked and ok is False:
            checks[label] = "failed"
            details.append(f"{label}: expected blocked but node present ({err})")
        elif ok is None and want_blocked:
            checks[label] = "unknown"
            details.append(f"{label}: probe unavailable ({err})")
        else:
            checks[label] = "allowed"

    if not policy.network:
        ok, err = _run_sbprobe(sb, _PROBE_NET_HIDDEN)
        if ok is True:
            checks["network"] = "verified"
        elif ok is False:
            checks["network"] = "failed"
            details.append("network: interfaces visible under a blocked-network policy")
        else:
            checks["network"] = "unknown"
            details.append("network: probe unavailable")
    else:
        checks["network"] = "allowed"

    verified = all(
        v == "verified" or v == "allowed"
        for k, v in checks.items()
        if k not in ("backend", "verified", "smoke")
    )
    if any(v == "failed" for v in checks.values()):
        summary = "not-verified"
    elif verified and not any(v == "limited" for v in checks.values()):
        summary = "verified"
    elif any(v == "unknown" for v in checks.values()):
        summary = "partial"
    else:
        summary = "partial"
    return {"backend": "bwrap", "verified": verified, "checks": checks,
            "summary": summary, "detail": details}


def _not_verified(reason: str) -> dict:
    return {
        "backend": "bwrap",
        "verified": None,
        "checks": {},
        "summary": "not-verified",
        "detail": [reason],
    }
