"""Fail-open regressions: an unlaunchable sandbox must NEVER degrade to a
quiet unsandboxed run.

* launcher.run_app_headless printed a "launching unsandboxed" warning.
* manager._run_app replaced a failed sandbox launch with a direct Popen.
"""

import os
import sys
from unittest import mock

import niruvi.core.sandbox_manager as sm_mod
import niruvi.launcher as launcher
from niruvi.core.policy import preset_default
from niruvi.core.sandbox_manager import LaunchResult, SandboxManager
from niruvi.desktop.installation_registry import InstallationRecord, InstallationRegistry


def _make_record(tmp_path, **kw):
    rec = InstallationRecord(name="Test.App", path=str(tmp_path))
    for k, v in kw.items():
        setattr(rec, k, v)
    return rec


class _RefusingBackend:
    name = "fake"

    def available(self):
        return True

    def detect_support(self):
        return {"available": True}

    def capabilities(self):
        return {}

    def launch_application(self, *a, **k):
        raise sm_mod.LaunchRefusedError("refusal for audit")


class _RegistryStub:
    def __init__(self, rec):
        self._rec = rec

    def get(self, name):
        return self._rec

    def lookup_by_name(self, name):
        return self._rec


class TestHeadlessFailClosed:
    def _setup_record(self, tmp_path, monkeypatch):
        os.makedirs(tmp_path, exist_ok=True)
        apprun = os.path.join(tmp_path, "AppRun")
        with open(apprun, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(apprun, 0o755)
        rec = _make_record(tmp_path, run_args="")
        monkeypatch.setattr(InstallationRegistry, "get", lambda self, name: rec)
        monkeypatch.setattr(InstallationRegistry, "lookup_by_name", lambda self, name: rec)
        return rec

    def test_refused_backend_exits_nonzero_and_writes_stderr(self, tmp_path, monkeypatch, capsys):
        self._setup_record(tmp_path, monkeypatch)

        class FakeMgr:
            def resolve_policy(self, app, record):
                return preset_default("Test.App", "bwrap")

            def launch(self, *a, **k):
                return LaunchResult(ok=False, refusals=["bwrap unusable: not found"])

        monkeypatch.setattr(sm_mod, "get_sandbox_manager", lambda: FakeMgr())
        monkeypatch.setattr(sys, "argv", ["niruvi"])
        rc = launcher.run_app_headless("Test.App")
        err = capsys.readouterr().err
        assert rc == 2, err
        assert "refused" in err.lower() and "bwrap unusable" in err

    def test_policy_resolution_error_exits_nonzero(self, tmp_path, monkeypatch, capsys):
        self._setup_record(tmp_path, monkeypatch)

        class FakeMgr:
            def resolve_policy(self, app, record):
                raise sm_mod.PolicyResolutionError("corrupt mirror")

        monkeypatch.setattr(sm_mod, "get_sandbox_manager", lambda: FakeMgr())
        monkeypatch.setattr(sys, "argv", ["niruvi"])
        rc = launcher.run_app_headless("Test.App")
        err = capsys.readouterr().err
        assert rc == 3
        assert "corrupt mirror" in err

    def test_explicit_unsandboxed_flag_records_reason(self, tmp_path, monkeypatch, capsys):
        self._setup_record(tmp_path, monkeypatch)
        captured = {}

        class FakeMgr:
            def resolve_policy(self, app, record):
                return preset_default("Test.App", "bwrap")

            def launch(self, *a, **k):
                captured["override_reason"] = k.get("override_reason", "")
                return LaunchResult(ok=True, pid=1, sandboxed=False, backend="none", proc=None)

        monkeypatch.setattr(sm_mod, "get_sandbox_manager", lambda: FakeMgr())
        monkeypatch.setattr(sys, "argv", ["niruvi", "--unsandboxed"])
        rc = launcher.run_app_headless("Test.App")
        assert rc == 0
        assert "explicit --unsandboxed CLI override" in captured["override_reason"]


class TestRegistryLaunchFailClosed:
    def test_backend_refusal_produces_refusal_not_popen(self, tmp_path):
        mgr = SandboxManager()
        with mock.patch.object(sm_mod, "get_backend", lambda *a, **k: _RefusingBackend()):
            res = mgr.launch("Test.App", str(tmp_path), ["/bin/true"], preset_default("Test.App", "bwrap"))
        assert res.ok is False
        assert any("refusal" in r for r in res.refusals)
        assert res.pid is None

    def test_manager_refusal_logged_as_refused_entry(self, tmp_path):
        from niruvi.core import sandbox_audit

        mgr = SandboxManager()
        with mock.patch.object(sm_mod, "get_backend", lambda *a, **k: _RefusingBackend()):
            mgr.launch("Audit.App", str(tmp_path), ["/bin/true"], preset_default("Audit.App", "bwrap"))
        entries = sandbox_audit.read_launches()
        assert entries and entries[0]["result"] == "refused"
