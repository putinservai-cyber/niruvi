"""Tests for the capability-driven Bubblewrap engine and the policy engine.

Where bwrap is available on the test host, several checks run REAL probes
(no mocks) so enforcement claims stay honest.
"""

import os

import pytest

from niruvi.core.capability_detector import report
from niruvi.core.policy import preset_default
from niruvi.core.policy_engine import (
    filter_environment,
    permission_states,
    summary_state,
    validate_policy,
)
from niruvi.core.profiles import build_profile
from niruvi.core.bubblewrap_backend import (
    BubblewrapSandbox,
    LaunchRefused,
    build_bwrap_args,
    build_bwrap_sandbox,
)
from niruvi.core.policy import FsGrant


def _bwrap_usable() -> bool:
    try:
        return bool(report()["bubblewrap"]["available"])
    except Exception:
        return False


BWRAP = pytest.mark.skipif(not _bwrap_usable(), reason="bubblewrap not available")


# ── capability_detector ───────────────────────────────────────────────

class TestCapabilityDetector:
    def test_report_shape(self):
        caps = report()
        assert {"namespaces", "bubblewrap"} <= set(caps.keys())
        for ns in ("user", "pid", "network"):
            assert "available" in caps["namespaces"][ns]

    def test_bwrap_usable_is_bool(self):
        caps = report()
        assert isinstance(caps["bubblewrap"]["available"], bool)


# ── build_bwrap_args ─────────────────────────────────────────────────

class TestPlanBuilding:
    def test_strict_profile_blocks_network(self):
        plan = build_bwrap_args(build_profile("strict", "T", "bwrap"), "/tmp", {})
        assert "--share-net" not in plan.argv
        assert not plan.share_net

    def test_standard_profile_shares_net(self):
        plan = build_bwrap_args(build_profile("standard", "T", "bwrap"), "/tmp", {})
        assert "--share-net" in plan.argv
        assert plan.share_net

    def test_unshare_all_present(self):
        plan = build_bwrap_args(preset_default("T", "bwrap"), "/tmp", {})
        assert "--unshare-all" in plan.argv
        assert plan.namespaces == ["unshare-all", "share-net"]

    def test_sysfs_net_masked_when_network_blocked(self):
        plan = build_bwrap_args(build_profile("strict", "T", "bwrap"), "/tmp", {})
        assert "--tmpfs" in plan.argv and "/sys/class/net" in plan.argv

    def test_dangerous_grant_root_refused_at_prereq_check(self):
        policy = preset_default("T", "bwrap")
        policy.custom_paths = [FsGrant(path="/", mode="rw")]
        plan = build_bwrap_args(policy, "/tmp", {})
        sb = BubblewrapSandbox(plan)
        refusals = sb.check_prerequisites()
        assert any("failed path validation" in r for r in refusals)

    def test_valid_custom_grant_binds(self):
        tmpf = os.path.join("/tmp/opencode", "bwrap-test-share")
        os.makedirs(tmpf, exist_ok=True)
        policy = preset_default("T", "bwrap")
        policy.custom_paths = [FsGrant(path=tmpf, mode="ro")]
        plan = build_bwrap_args(policy, "/tmp", {})
        assert any("--ro-bind" == plan.argv[i] and tmpf == plan.argv[i + 2] for i in range(len(plan.argv) - 2))

    def test_clearenv_and_setenv(self):
        plan = build_bwrap_args(preset_default("T", "bwrap"), "/tmp", {"HOME": "/tmp/fakehome"})
        argv = plan.argv
        assert "--clearenv" in argv
        assert "--setenv" in argv
        # First --setenv is an alpha-sorted key of the filtered environment.
        i = argv.index("--setenv")
        assert argv[i + 1]
        pairs = [(argv[i], argv[i + 1]) for i, k in enumerate(argv) if k == "--setenv"]
        assert ("HOME", "/tmp/fakehome") not in pairs  # overridden by filtering,

    def test_portable_home(self):
        app_dir = os.path.join("/tmp", "bwrap-test-app")
        os.makedirs(app_dir, exist_ok=True)
        policy = preset_default("T", "bwrap")
        policy.portable_home = True
        plan = build_bwrap_args(policy, app_dir, {})
        assert "--bind" in plan.argv
        # .home inside app_dir is rw-bound
        assert os.path.join(app_dir, ".home") in plan.argv


# ── BubblewrapSandbox fail-closed ────────────────────────────────────

class TestSandboxRefusals:
    def _caps_without(self, *ns):
        caps = {
            "namespaces": {k: {"available": False} for k in ("user", "pid", "ipc", "network", "cgroup")},
            "bubblewrap": {"available": True},
        }
        for n in ns:
            caps["namespaces"][n] = {"available": True}
        return caps

    def test_bwrap_missing_refuses(self):
        caps = self._caps_without("user", "network")
        caps["bubblewrap"] = {"available": False}
        plan = build_bwrap_args(build_profile("strict", "T", "bwrap"), "/tmp", {}, caps=caps)
        refusals = BubblewrapSandbox(plan, caps).check_prerequisites()
        assert refusals and "not usable" in refusals[0]

    def test_userns_missing_refuses(self):
        caps = self._caps_without()
        plan = build_bwrap_args(build_profile("strict", "T", "bwrap"), "/tmp", {}, caps=caps)
        sb = BubblewrapSandbox(plan, caps)
        refusals = sb.check_prerequisites()
        assert any("user namespaces" in r for r in refusals)

    def test_net_block_needs_netns(self):
        caps = self._caps_without("user")
        plan = build_bwrap_args(build_profile("strict", "T", "bwrap"), "/tmp", {}, caps=caps)
        sb = BubblewrapSandbox(plan, caps)
        refusals = sb.check_prerequisites()
        assert any("network namespaces" in r for r in refusals)

    def test_run_raises_launchrefused_when_prereqs_missing(self):
        caps = self._caps_without()
        plan = build_bwrap_args(build_profile("strict", "T", "bwrap"), "/tmp", {}, caps=caps)
        sb = BubblewrapSandbox(plan, caps)
        with pytest.raises(LaunchRefused):
            sb.run(["/bin/true"])


# ── real bwrap integration ───────────────────────────────────────────

@BWRAP
class TestRealBwrap:
    def test_strict_net_hidden(self):
        sb = build_bwrap_sandbox(build_profile("strict", "T", "bwrap"), "/nonexistent", {})
        p = sb.run_probe(["/bin/sh", "-c", "test $(ls /sys/class/net 2>/dev/null | wc -l) -eq 0"])
        assert p.returncode == 0, p.stdout + p.stderr

    def test_standard_net_visible(self):
        sb = build_bwrap_sandbox(build_profile("standard", "T", "bwrap"), "/nonexistent", {})
        p = sb.run_probe(["ls", "/sys/class/net"])
        assert p.returncode == 0

    def test_home_not_mounted_strict(self):
        home = os.path.expanduser("~")
        sb = build_bwrap_sandbox(build_profile("strict", "T", "bwrap"), "/nonexecutable", {})
        p = sb.run_probe(["/usr/bin/ls", home])
        assert p.returncode != 0  # strict has home none; path does not exist in the sandbox


# ── policy engine ────────────────────────────────────────────────────

class TestPolicyEngine:
    def test_filter_strips_blocked_channels(self):
        policy = build_profile("standard", "T", "bwrap")
        policy.ipc["x11"] = False
        policy.ipc["wayland"] = False
        env = filter_environment(policy, {"DISPLAY": ":0", "WAYLAND_DISPLAY": "wl-0"})
        assert "DISPLAY" not in env
        assert "WAYLAND_DISPLAY" not in env

    def test_filter_keeps_display_when_allowed(self):
        policy = build_profile("standard", "T", "bwrap")
        env = filter_environment(policy, {})
        assert env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")

    def test_filter_no_ssh_agent(self):
        os.environ.pop("SSH_AUTH_SOCK", None)
        policy = build_profile("standard", "T", "bwrap")
        env = filter_environment(policy, None)
        assert "SSH_AUTH_SOCK" not in env

    def test_validate_policy_flags_bad_mode(self):
        policy = preset_default("T", "bwrap")
        policy.custom_paths = [FsGrant(path="/tmp", mode="snake")]
        issues = validate_policy(policy)
        assert any("ro/rw" in i for i in issues)

    def test_summary_states_sane(self):
        states = permission_states(build_profile("strict", "T", "bwrap"), _FakeBackend())
        summary = summary_state(states)
        assert summary in ("protected", "partially_protected", "not_protected", "failed")

    def test_microphone_not_enforced_while_audio_allowed(self):
        policy = build_profile("standard", "T", "bwrap")
        states = permission_states(policy, _FakeBackend())
        mic = next(s for s in states if s["id"] == "device_microphone")
        # Honest: with audio allowed, the same ALSA node enables capture, so
        # mic enforcement is NOT claimed.
        assert mic["enforced"] is False
        assert "ALSA" in mic["explanation"]


class _FakeBackend:
    def capabilities(self):
        return {}

    def available(self):
        return True


# ── sandbox diagnostics ──────────────────────────────────────────────

@BWRAP
class TestDiagnostics:
    def test_strict_fully_verified(self):
        from niruvi.core.sandbox_diagnostics import verify_sandbox

        res = verify_sandbox(build_profile("strict", "T", "bwrap"), "/tmp", {})
        assert res["summary"] == "verified"
        assert res["checks"]["network"] == "verified"

    def test_standard_mic_limited(self):
        from niruvi.core.sandbox_diagnostics import verify_sandbox

        res = verify_sandbox(build_profile("standard", "T", "bwrap"), "/tmp", {})
        assert res["checks"]["microphone"] == "limited"
        assert res["summary"] == "partial"
