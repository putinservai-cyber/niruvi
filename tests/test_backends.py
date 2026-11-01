"""Tests for the sandbox backend abstraction and enforcement reporting."""

import os

import pytest

from niruvi.core import backends
from niruvi.core.policy import FsGrant, SandboxPolicy, preset_default
from niruvi.core.policy_store import PolicyStore


@pytest.fixture(autouse=True)
def _clear_cache():
    backends.clear_backend_cache()
    yield
    backends.clear_backend_cache()


class TestCapabilities:
    def test_shield_capabilities_present(self):
        caps = backends.ShieldBackend().capabilities()
        for key in ("filesystem", "network", "devices", "ipc", "environment", "resources"):
            assert key in caps
            assert caps[key].level in (backends.SUPPORTED, backends.PARTIAL, backends.UNSUPPORTED)
        assert caps["environment"].level == backends.SUPPORTED

    def test_shield_devices_unsupported(self):
        assert backends.ShieldBackend().capability("devices").level == backends.UNSUPPORTED

    def test_firejail_unavailable_is_unsupported(self, monkeypatch):
        monkeypatch.setattr(
            "niruvi.core.sandbox.check_firejail_available",
            lambda: {"available": False, "error": "not found"},
        )
        backends.clear_backend_cache()
        fb = backends.FirejailBackend()
        assert fb.available() is False
        assert fb.capability("filesystem").level == backends.UNSUPPORTED


class TestSelection:
    def test_unknown_falls_back_to_shield(self):
        assert isinstance(backends.get_backend("nope"), backends.ShieldBackend)

    def test_strict_unknown_returns_shield(self):
        assert isinstance(backends.get_backend("nope", strict=True), backends.ShieldBackend)

    def test_unavailable_non_strict_falls_back(self, monkeypatch):
        monkeypatch.setattr(
            "niruvi.core.sandbox.check_bwrap_available",
            lambda: {"available": False, "error": "not found"},
        )
        backends.clear_backend_cache()
        assert isinstance(backends.get_backend("bwrap"), backends.ShieldBackend)

    def test_unavailable_strict_keeps_backend(self, monkeypatch):
        monkeypatch.setattr(
            "niruvi.core.sandbox.check_bwrap_available",
            lambda: {"available": False, "error": "not found"},
        )
        backends.clear_backend_cache()
        assert isinstance(backends.get_backend("bwrap", strict=True), backends.BubblewrapBackend)


class TestEnforcementStatus:
    def test_disabled_not_sandboxed(self):
        status = backends.ShieldBackend().get_enforcement_status(SandboxPolicy(enabled=False))
        assert status.state == backends.NOT_SANDBOXED

    def test_pending_state(self):
        status = backends.ShieldBackend().get_enforcement_status(SandboxPolicy(enabled=True), pending=True)
        assert status.state == backends.PENDING_CHANGES

    def test_config_error(self):
        p = SandboxPolicy(enabled=True, home_access="sometimes")
        status = backends.ShieldBackend().get_enforcement_status(p)
        assert status.state == backends.CONFIG_ERROR

    def test_enabled_is_sandboxed_or_partial(self):
        status = backends.ShieldBackend().get_enforcement_status(preset_default("Demo"))
        assert status.state in (backends.SANDBOXED, backends.PARTIALLY_RESTRICTED)

    def test_firejail_unavailable_status(self, monkeypatch):
        monkeypatch.setattr(
            "niruvi.core.sandbox.check_firejail_available",
            lambda: {"available": False, "error": "not found"},
        )
        backends.clear_backend_cache()
        status = backends.FirejailBackend().get_enforcement_status(SandboxPolicy(app_id="Demo"))
        assert status.state == backends.BACKEND_UNAVAILABLE


class TestValidate:
    def test_network_block_needs_namespace(self):
        p = SandboxPolicy(app_id="Demo", network=False, use_namespace=False)
        issues = backends.ShieldBackend().validate_policy(p)
        assert any("namespace" in i.lower() for i in issues)


class TestApplyRestore:
    def test_apply_persists_and_mirrors(self, tmp_path):
        store = PolicyStore(str(tmp_path / "policies"))
        p = SandboxPolicy(app_id="Demo", network=False)
        p.custom_paths = [FsGrant("/tmp/zniruvi-backend-a", "rw")]
        result = backends.ShieldBackend().apply_policy("Demo", p, record=None, store=store)
        assert result.ok is True
        assert store.load("Demo").network is False

    def test_restore_previous(self, tmp_path):
        store = PolicyStore(str(tmp_path / "policies"))
        original = SandboxPolicy(app_id="Demo", network=True)
        store.save("Demo", original)
        changed = SandboxPolicy(app_id="Demo", network=False)
        store.save("Demo", changed)
        ok = backends.ShieldBackend().restore_previous_policy("Demo", original, None, store)
        assert ok is True
        assert store.load("Demo").network is True

    def test_apply_invalid_returns_error(self, tmp_path):
        store = PolicyStore(str(tmp_path / "policies"))
        p = SandboxPolicy(app_id="Demo")
        p.env = {"bad name": "x"}
        result = backends.ShieldBackend().apply_policy("Demo", p, record=None, store=store)
        assert result.ok is False


class TestLaunch:
    def test_disabled_launch_runs_without_extraction(self, tmp_path, qapp):
        app = tmp_path / "Demo"
        app.mkdir()
        (app / "AppRun").write_text("#!/bin/sh\nexit 0\n")
        before = sorted(os.listdir(app))
        policy = SandboxPolicy(app_id="Demo", enabled=False)
        proc = backends.ShieldBackend().launch_application(str(app), ["/bin/true"], {}, policy)
        proc.wait(5)
        assert sorted(os.listdir(app)) == before

    def test_all_backend_status_shape(self):
        status = backends.all_backend_status()
        for name in ("shield", "firejail", "bwrap"):
            assert name in status
            assert "capabilities" in status[name]
