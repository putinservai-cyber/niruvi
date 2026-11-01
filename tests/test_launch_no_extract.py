"""Verify launching an installed app never extracts or copies application files.

Niruvi's normal launch runs the already-installed ``<app_dir>/AppRun``; the
sandbox only re-points environment/paths.  These tests lock that in so a future
change cannot silently reintroduce permanent extraction at launch time.
"""

import os

import niruvi.core.broker as broker_mod
import niruvi.core.sandbox as sandbox


def _make_app(tmp_path):
    app = tmp_path / "Demo"
    app.mkdir()
    apprun = app / "AppRun"
    apprun.write_text("#!/bin/sh\nexit 0\n")
    os.chmod(apprun, 0o755)
    return app


def _snapshot(app):
    return sorted(os.listdir(app))


def test_unsandboxed_launch_does_not_extract(tmp_path, qapp):
    app = _make_app(tmp_path)
    before = _snapshot(app)

    sb = sandbox.Shield(sandbox.ShieldConfig(enabled=False))
    proc = sb.run(["/bin/true"], cwd=str(app), env={})
    proc.wait(5)

    assert _snapshot(app) == before


def test_shield_launch_does_not_extract(tmp_path, qapp, monkeypatch):
    app = _make_app(tmp_path)
    before = _snapshot(app)

    # Avoid spawning the host helper daemons during the test.
    monkeypatch.setattr(sandbox, "_get_xdg_open_daemon", lambda: None)
    monkeypatch.setattr(broker_mod, "get_daemon", lambda: None)

    config = sandbox.ShieldConfig(
        enabled=True,
        hardening=False,
        landlock=False,
        use_namespace=False,
        backend=sandbox.SandboxBackend.SHIELD,
    )
    sb = sandbox.Shield(config)
    proc = sb.run(["/bin/true"], cwd=str(app), env={})
    proc.wait(5)

    assert _snapshot(app) == before
