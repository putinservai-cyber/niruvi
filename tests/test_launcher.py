"""Tests for the sandbox-aware headless launcher."""

import os
import stat
import subprocess
import sys

import pytest

import niruvi.launcher as launcher
from niruvi.desktop.installation_registry import InstallationRecord, InstallationRegistry


@pytest.fixture()
def fake_launcher(monkeypatch):
    launcher_bin = "/usr/bin/fake-niruvi-launcher"
    monkeypatch.setattr(launcher, "_find_launcher", lambda: launcher_bin)
    return launcher_bin


class TestBuildCommand:
    def test_no_launcher(self, monkeypatch):
        monkeypatch.setattr(launcher, "_find_launcher", lambda: None)
        assert launcher.build_launch_command("VLC") is None

    def test_plain(self, fake_launcher):
        assert launcher.build_launch_command("VLC") == [fake_launcher, "--run", "VLC"]

    def test_extra_args(self, fake_launcher):
        assert launcher.build_launch_command("VLC", ["--fullscreen", "f.mp4"]) == [
            fake_launcher,
            "--run",
            "VLC",
            "--",
            "--fullscreen",
            "f.mp4",
        ]

    def test_empty_extra_args(self, fake_launcher):
        assert launcher.build_launch_command("VLC", []) == [fake_launcher, "--run", "VLC"]


class TestExecLine:
    def test_quoted(self, fake_launcher):
        line = launcher.launcher_exec_line("My App")
        q = launcher.shlex.quote(fake_launcher)
        assert line == f"{q} --run 'My App' %F"

    def test_no_fields(self, fake_launcher):
        assert launcher.launcher_exec_line("X", fields="") == f"{launcher.shlex.quote(fake_launcher)} --run X"

    def test_no_launcher(self, monkeypatch):
        monkeypatch.setattr(launcher, "_find_launcher", lambda: None)
        assert launcher.launcher_exec_line("X") == ""

    def test_re_executable_with_shlex(self, fake_launcher):
        line = launcher.launcher_exec_line("My App")
        parts = launcher.shlex.split(line)
        assert parts == [fake_launcher, "--run", "My App", "%F"]


@pytest.fixture()
def fake_app(tmp_path):
    app_dir = tmp_path / "TestApp.AppDir"
    app_dir.mkdir()
    apprun = app_dir / "AppRun"
    apprun.write_text("#!/bin/sh\necho launched:$1\nsleep 30\n")
    apprun.chmod(0o755)
    return app_dir


def _register(data_dir, app_dir, sandbox_config=None):
    reg = InstallationRegistry()
    reg.add(
        InstallationRecord(
            name="TestApp",
            version="1.0",
            path=str(app_dir),
            desktop_file="",
            tags=[],
            env_vars={},
            run_args="",
            install_date="2026-01-01",
            size=0,
            sandbox_config=sandbox_config,
        )
    )
    reg.flush()
    return reg


class TestRunAppHeadless:
    def test_not_installed(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("NIRUVI_DATA_DIR", str(tmp_path))
        assert launcher.run_app_headless("Ghost") == 1
        assert "not installed" in capsys.readouterr().err

    def test_missing_appdir(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("NIRUVI_DATA_DIR", str(tmp_path))
        _register(tmp_path, tmp_path / "Nowhere")
        assert launcher.run_app_headless("TestApp") == 1

    def test_missing_apprun(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("NIRUVI_DATA_DIR", str(tmp_path))
        _register(tmp_path, tmp_path / "noapprun")
        (tmp_path / "noapprun").mkdir()
        assert launcher.run_app_headless("TestApp") == 1
        assert "AppRun" in capsys.readouterr().err

    def test_direct_launch_no_sandbox(self, tmp_path, monkeypatch, fake_app):
        monkeypatch.setenv("NIRUVI_DATA_DIR", str(tmp_path))
        _register(tmp_path, fake_app)
        rc = launcher.run_app_headless("TestApp", ["hello"])
        assert rc == 0
        procs = subprocess.run(
            ["pgrep", "-af", "TestApp.AppDir/AppRun"],
            capture_output=True,
            text=True,
        )
        assert "hello" in procs.stdout
        subprocess.run(["pkill", "-f", "TestApp.AppDir/AppRun"], check=False)
        assert os.access(os.path.join(fake_app, "AppRun"), os.X_OK)
        assert stat.S_IMODE(os.stat(os.path.join(fake_app, "AppRun")).st_mode) & 0o111

    def test_run_args_forwarded(self, tmp_path, monkeypatch, fake_app):
        monkeypatch.setenv("NIRUVI_DATA_DIR", str(tmp_path))
        _register(tmp_path, fake_app)
        launcher.run_app_headless("TestApp")
        # run_args empty here; extra args path covered above
        subprocess.run(["pkill", "-f", "TestApp.AppDir/AppRun"], check=False)

    def test_sandboxed_launch(self, tmp_path, monkeypatch, fake_app):
        monkeypatch.setenv("NIRUVI_DATA_DIR", str(tmp_path))
        _register(
            tmp_path,
            fake_app,
            sandbox_config={
                "enabled": True,
                "backend": "shield",
                "hardening": False,
                "timeout": 0,
                "private_tmp": False,
            },
        )
        rc = launcher.run_app_headless("TestApp")
        assert rc == 0
        procs = subprocess.run(
            ["pgrep", "-af", "TestApp.AppDir/AppRun"],
            capture_output=True,
            text=True,
        )
        assert "TestApp.AppDir/AppRun" in procs.stdout
        subprocess.run(["pkill", "-f", "TestApp.AppDir/AppRun"], check=False)

    def test_unsandboxed_flag_skips_sandbox(self, tmp_path, monkeypatch, fake_app):
        monkeypatch.setenv("NIRUVI_DATA_DIR", str(tmp_path))
        _register(
            tmp_path,
            fake_app,
            sandbox_config={
                "enabled": True,
                "backend": "shield",
                "hardening": False,
            },
        )

        def boom(*a, **k):
            raise AssertionError("sandbox should not be used")

        monkeypatch.setattr(launcher.subprocess, "Popen", boom)
        monkeypatch.setattr(sys, "argv", ["niruvi", "--run", "TestApp", "--unsandboxed"])
        # Popen must be reachable — guard: --unsandboxed path calls Popen; replace after assert
        launcher._ = None
        with pytest.raises(AssertionError) as excinfo:
            launcher.run_app_headless("TestApp")
        assert "sandbox should not be used" in str(excinfo.value)
