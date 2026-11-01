"""Tests for wiring a SandboxPolicy into the sandbox config/launcher."""

import os

import pytest

from niruvi.core.policy import FsGrant, SandboxPolicy
from niruvi.core.sandbox import Shield, ShieldConfig


class TestConfigFromPolicy:
    def test_maps_core_fields(self):
        p = SandboxPolicy(app_id="Demo", network=False, home_access="ro", private_tmp=True)
        p.custom_paths = [FsGrant("/tmp/zniruvi-x", "rw")]
        cfg = ShieldConfig.from_policy(p)
        assert cfg.enabled is True
        assert cfg.enable_network is False
        assert cfg.private_tmp is True
        assert "/tmp/zniruvi-x" in cfg.fs_readwrite

    def test_home_access_none_grants_no_home(self, tmp_path):
        p = SandboxPolicy(app_id="Demo", home_access="none")
        p.dirs = dict.fromkeys(p.dirs, "none")
        cfg = ShieldConfig.from_policy(p)
        assert cfg.fs_readwrite is not None
        # No broad home grant present.
        import os

        home = os.path.expanduser("~")
        assert home not in cfg.fs_readwrite

    def test_denied_paths_carried(self):
        p = SandboxPolicy(app_id="Demo")
        p.custom_paths = [FsGrant("/tmp/zniruvi-denied", "deny")]
        cfg = ShieldConfig.from_policy(p)
        assert "/tmp/zniruvi-denied" in cfg.fs_denied

    def test_dict_roundtrip_preserves_fs(self):
        p = SandboxPolicy(app_id="Demo", network=False)
        p.custom_paths = [FsGrant("/tmp/zniruvi-rt", "ro")]
        cfg = ShieldConfig.from_policy(p)
        again = ShieldConfig.from_dict(cfg.to_dict())
        assert again.fs_readonly == cfg.fs_readonly
        assert again.fs_readwrite == cfg.fs_readwrite
        assert again.enable_network is False


class TestIpcEnv:
    def test_disabling_channels_strips_vars(self):
        cfg = ShieldConfig(
            enabled=True,
            ipc={"x11": False, "wayland": False, "dbus_session": False, "ssh_agent": False, "dbus_system": True},
            devices={"audio": False},
        )
        shield = Shield(cfg)
        env = {
            "DISPLAY": ":0",
            "WAYLAND_DISPLAY": "wayland-0",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=x",
            "SSH_AUTH_SOCK": "/tmp/agent",
            "PULSE_SERVER": "/run/pulse",
        }
        shield._apply_ipc_env(env)
        assert "DISPLAY" not in env
        assert "WAYLAND_DISPLAY" not in env
        assert "DBUS_SESSION_BUS_ADDRESS" not in env
        assert "SSH_AUTH_SOCK" not in env
        assert "PULSE_SERVER" not in env

    def test_no_ipc_config_is_noop(self):
        shield = Shield(ShieldConfig(enabled=True))
        env = {"DISPLAY": ":0"}
        shield._apply_ipc_env(env)
        assert env["DISPLAY"] == ":0"


class TestLegacyCompatibility:
    def test_empty_config_still_constructs(self):
        cfg = ShieldConfig.from_dict({})
        assert cfg.fs_readonly is None
        assert cfg.fs_readwrite is None

    def test_defaults_legacy_landlock_when_no_explicit_fs(self):
        # fs_* None means "legacy defaults" — the launcher still adds home etc.
        cfg = ShieldConfig(enabled=True, landlock=True)
        assert cfg.fs_readonly is None
        assert cfg.fs_readwrite is None


def test_apply_ipc_env_requires_dict():
    shield = Shield(ShieldConfig(enabled=True, ipc={}))
    env = {"DISPLAY": ":0"}
    shield._apply_ipc_env(env)
    assert env == {"DISPLAY": ":0"}


def test_from_policy_invalid_backend_still_builds():
    p = SandboxPolicy(app_id="Demo", backend="whatever")
    cfg = ShieldConfig.from_policy(p)
    assert cfg.backend == "whatever"


@pytest.mark.parametrize("mode", ["ro", "rw", "none"])
def test_home_access_modes(mode):
    cfg = ShieldConfig.from_policy(SandboxPolicy(app_id="X", home_access=mode))
    if mode == "none":
        assert os.path.expanduser("~") not in (cfg.fs_readwrite or [])
    elif mode == "ro":
        assert os.path.expanduser("~") in (cfg.fs_readonly or [])
    else:
        assert os.path.expanduser("~") in (cfg.fs_readwrite or [])
