"""Tests for the permission policy model (parsing, validation, presets, diff)."""

import os

import pytest

from niruvi.core.policy import (
    POLICY_SCHEMA_VERSION,
    FsGrant,
    PolicyError,
    SandboxPolicy,
    dangerous_changes,
    diff_policies,
    preset_gaming,
    preset_media_editing,
    preset_restricted,
    validate_env_name,
    validate_env_value,
    validate_path,
)


class TestPathValidation:
    def test_relative_rejected(self):
        with pytest.raises(PolicyError):
            validate_path("relative/path")

    def test_empty_rejected(self):
        with pytest.raises(PolicyError):
            validate_path("")

    def test_traversal_rejected(self):
        with pytest.raises(PolicyError):
            validate_path("/home/user/../../etc")

    def test_null_byte_rejected(self):
        with pytest.raises(PolicyError):
            validate_path("/tmp/evil\x00name")

    def test_symlink_resolved(self, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        os.symlink(real, link)
        resolved = validate_path(str(link / "sub"))
        assert resolved.startswith(os.path.realpath(real) + os.sep) or resolved == os.path.realpath(real)


class TestEnvValidation:
    def test_valid_name(self):
        assert validate_env_name("QT_QPA_PLATFORM") == "QT_QPA_PLATFORM"

    @pytest.mark.parametrize("bad", ["", "1BAD", "with-dash", "with space", "a;b"])
    def test_invalid_name(self, bad):
        with pytest.raises(PolicyError):
            validate_env_name(bad)

    def test_null_value_rejected(self):
        with pytest.raises(PolicyError):
            validate_env_value("x\x00y")


class TestSerialization:
    def test_roundtrip(self):
        p = SandboxPolicy(app_id="Demo", network=False, home_access="ro")
        p.custom_paths = [FsGrant("/tmp/data", "rw")]
        p.env = {"FOO": "bar"}
        p.devices["camera"] = False
        q = SandboxPolicy.from_dict(p.to_dict())
        assert q == p or q.to_dict() == p.to_dict()
        assert q.schema_version == POLICY_SCHEMA_VERSION

    def test_unknown_schema_reads_best_effort(self):
        data = SandboxPolicy(app_id="x").to_dict()
        data["schema_version"] = 999
        p = SandboxPolicy.from_dict(data)
        assert p.app_id == "x"

    def test_from_none_defaults(self):
        p = SandboxPolicy.from_dict(None)
        assert p.app_id == ""


class TestValidation:
    def test_valid_policy(self):
        p = SandboxPolicy(app_id="Demo")
        assert p.validate() == []

    def test_bad_mode(self):
        p = SandboxPolicy(app_id="Demo")
        p.home_access = "sometimes"
        assert p.validate()

    def test_duplicate_custom_path(self):
        p = SandboxPolicy(app_id="Demo")
        p.custom_paths = [FsGrant("/tmp/a", "rw"), FsGrant("/tmp/a", "ro")]
        assert any("Duplicate" in i for i in p.validate())

    def test_root_path_rejected(self):
        p = SandboxPolicy(app_id="Demo")
        p.custom_paths = [FsGrant("/", "rw")]
        assert any("'/" in i for i in p.validate())

    def test_normalized_dedupes_and_validates_env(self):
        p = SandboxPolicy(app_id="Demo")
        p.custom_paths = [FsGrant("/tmp/zniruvi-policy-a", "rw")]
        p.env = {"GOOD": "1"}
        clean = p.normalized()
        assert len(clean.custom_paths) == 1

    def test_normalized_rejects_invalid(self):
        p = SandboxPolicy(app_id="Demo")
        p.env = {"bad name": "1"}
        with pytest.raises(PolicyError):
            p.normalized()


class TestPresets:
    def test_restricted_is_tight(self):
        p = preset_restricted("Demo")
        assert p.network is False
        assert p.home_access == "none"
        assert all(m == "none" for m in p.dirs.values())

    def test_default_is_usable(self):
        from niruvi.core.policy import preset_default

        p = preset_default("Demo")
        assert p.enabled is True
        assert p.network is True

    def test_media_grants_camera_but_no_network(self):
        p = preset_media_editing("Demo")
        assert p.devices["camera"] is True
        assert p.network is False

    def test_gaming_keeps_gpu(self):
        p = preset_gaming("Demo")
        assert p.devices["gpu"] is True

    def test_all_presets_validate(self):
        from niruvi.core.policy import PRESETS

        for _key, (_label, fn) in PRESETS.items():
            assert fn("Demo").validate() == []


class TestDiff:
    def test_grants_detected(self):
        current = preset_restricted("Demo")
        proposed = preset_media_editing("Demo")
        d = diff_policies(current, proposed)
        assert d["grants"] is True
        assert d["changes"]

    def test_revokes_detected(self):
        from niruvi.core.policy import preset_default

        d = diff_policies(preset_default("Demo"), preset_restricted("Demo"))
        assert d["revokes"] is True

    def test_no_change(self):
        from niruvi.core.policy import preset_default

        p = preset_default("Demo")
        d = diff_policies(p, p.copy())
        assert d["changes"] == []

    def test_dangerous_env_warns(self):
        p = SandboxPolicy(app_id="Demo")
        p.env = {"LD_PRELOAD": "/tmp/x.so"}
        warns = dangerous_changes(p)
        assert any("LD_PRELOAD" in w for w in warns)

    def test_disabled_warns(self):
        p = SandboxPolicy(app_id="Demo", enabled=False)
        assert dangerous_changes(p)
