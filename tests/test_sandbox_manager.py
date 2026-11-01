"""SandboxManager: fail-closed semantics, pid guards, audit trail."""

import os
import stat

import pytest

from niruvi.core import sandbox_audit
from niruvi.core.profiles import build_profile
from niruvi.core.sandbox_manager import (
    LaunchRefused,
    PolicyResolutionError,
    SandboxManager,
    get_sandbox_manager,
)


@pytest.fixture(autouse=True)
def clean_manager():
    get_sandbox_manager()._procs.clear()
    yield
    get_sandbox_manager()._procs.clear()


class TestResolvePolicy:
    def test_no_policy_defaults_to_standard_sandbox(self):
        mgr = SandboxManager()
        p = mgr.resolve_policy("Missing.App", None)
        assert p.enabled and p.profile == "standard"

    def test_corrupt_mirror_raises(self):
        class R:
            sandbox_config = {"devices": "not-a-dict", "dirs": 42}

        mgr = SandboxManager()
        with pytest.raises(PolicyResolutionError):
            # fields coerce, so corrupt *types* must surface via enabled path
            mgr.resolve_policy("X", R())

    def test_corrupt_backend_refused(self):
        class R:
            sandbox_config = {"backend": None, "enabled": True}

            def __getattr__(self, k):
                return None

        mgr = SandboxManager()
        p = mgr.resolve_policy("X", R())
        assert p.enabled


class TestFailClosedLaunch:
    def _fake_backend(self, monkeypatch, instance):
        import niruvi.core.sandbox_manager as sm_mod

        class FakeB:
            name = "fake"
            display_name = "Fake"

            def available(self):
                return True

            def detect_support(self):
                return {"available": True}

            def capabilities(self):
                return {}

            def launch_application(self, app_dir, cmd, env, policy):
                return instance.launch(app_dir, cmd, env, policy)

        monkeypatch.setattr(
            sm_mod, "get_backend",
            lambda name, strict=False: FakeB(),
        )

    def test_backend_returning_none_is_refusal(self, monkeypatch):
        class I:
            def launch(self, *a, **k):
                return None

        self._fake_backend(monkeypatch, I())
        res = SandboxManager().launch(
            "App", "/tmp", ["/bin/true"], build_profile("standard", "App", "fake"),
        )
        assert not res.ok
        assert any("returned no process" in r for r in res.refusals)

    def test_backend_refusal_raises_launchrefused_up(self, monkeypatch):
        class I:
            def launch(self, *a, **k):
                raise LaunchRefused("cannot enforce network block")

        self._fake_backend(monkeypatch, I())
        res = SandboxManager().launch(
            "App", "/tmp", ["/bin/true"], build_profile("standard", "App", "fake"),
        )
        assert not res.ok
        assert any("cannot enforce network block" in r for r in res.refusals)
        # Never a quiet unsandboxed fallback
        assert res.sandboxed is False and res.pid is None

    def test_launch_refusal_recorded_in_audit(self, monkeypatch):
        class I:
            def launch(self, *a, **k):
                raise LaunchRefused("boom")

        self._fake_backend(monkeypatch, I())
        SandboxManager().launch(
            "Audited.App", "/tmp", ["/bin/true"], build_profile("standard", "Audited.App", "fake")
        )
        entries = sandbox_audit.read_launches()
        assert entries, "audit entry missing"
        assert entries[0]["result"] == "refused"

    def test_missing_app_dir_refused(self, monkeypatch):
        res = SandboxManager().launch(
            "App", "/nonexistent-dir-xyz", ["/bin/true"], build_profile("standard", "App", "fake")
        )
        assert not res.ok
        assert any("install directory missing" in r for r in res.refusals)


class TestRealLaunch:

    def test_real_bwrap_launch_and_stop(self):
        from niruvi.core.capability_detector import report

        if not report().get("bubblewrap", {}).get("available"):
            pytest.skip("bwrap unavailable")
        mgr = SandboxManager()
        policy = build_profile("strict", "Sleep", "bwrap")
        policy.network = False
        base = os.environ["NIRUVI_DATA_DIR"]
        app_dir = os.path.join(base, "app")
        os.makedirs(app_dir, exist_ok=True)
        res = mgr.launch("Sleep", app_dir, ["/bin/sleep", "60"], policy, executable="/bin/sleep")
        assert res.ok and res.sandboxed
        assert mgr.is_process_alive(res.pid)
        assert mgr.stop_app(res.pid)
        assert not mgr.is_process_alive(res.pid)

    def test_stop_refuses_foreign_and_pid1(self):
        mgr = SandboxManager()
        assert not mgr.stop_app(1)
        assert not mgr.stop_app(99999)


class TestAuditLog:
    def test_override_is_audited(self, tmp_path):
        sandbox_audit.record_launch(
            "App", "/bin/app", "standard", "bwrap", "override",
            pid=4242, override_reason="user asked in UI", base_dir=str(tmp_path),
        )
        entries = sandbox_audit.read_launches(base_dir=str(tmp_path))
        assert entries[0]["result"] == "override"
        assert entries[0]["override_reason"] == "user asked in UI"

    def test_log_is_0600(self, tmp_path):
        sandbox_audit.record_launch("A", "/x", "standard", "bwrap", "launched", base_dir=str(tmp_path))
        mode = stat.S_IMODE(os.stat(os.path.join(str(tmp_path), "sandbox_audit.log")).st_mode)
        assert mode == 0o600

    def test_rotation(self, tmp_path):
        path = os.path.join(str(tmp_path), "sandbox_audit.log")
        with open(path, "w") as f:
            f.write("x" * (sandbox_audit._MAX_BYTES + 10))
        sandbox_audit.record_launch("A", "/x", "standard", "bwrap", "launched", base_dir=str(tmp_path))
        assert os.path.exists(path + ".1")
        entries = sandbox_audit.read_launches(limit=1, base_dir=str(tmp_path))
        assert entries[0]["result"] == "launched"

    def test_singleton(self):
        assert get_sandbox_manager() is get_sandbox_manager()


class TestPermissionStatesThroughManager:
    def test_states_shape(self):
        mgr = SandboxManager()
        states, summary = mgr.permission_states(build_profile("standard", "App", "bwrap"))
        assert summary in ("protected", "partially_protected", "not_protected", "failed")
        for s in states:
            assert {"state", "configured", "enforced", "verified"} <= set(s.keys())
