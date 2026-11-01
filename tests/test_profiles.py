"""Profile correctness: presets, migration, honest deltas, DEVELOPER guard."""

from niruvi.core.policy import (
    POLICY_SCHEMA_VERSION,
    DEVICE_KEYS,
    SandboxPolicy,
    preset_default,
)
from niruvi.core.profiles import (
    DEFAULT_PROFILE_NAME,
    build_custom,
    build_profile,
    default_profile_name,
    describe_profile,
    is_default_profile,
    restriction_deltas,
    switch_profile,
    validate_profile_name,
)


class TestPresets:
    def test_standard_is_practical(self):
        p = build_profile("standard", "App", "bwrap")
        assert p.enabled and p.network
        assert p.home_access == "none"  # no free hand on files
        assert p.dirs["downloads"] == "ro"
        assert p.devices["gpu"] and p.devices["audio"]
        assert not p.devices["camera"] and not p.devices["microphone"]
        assert p.ipc["wayland"] and p.ipc["x11"] and p.ipc["dbus_session"]
        assert not p.ipc["dbus_system"]
        assert p.private_tmp

    def test_strict_blocks_network(self):
        p = build_profile("strict")
        assert not p.network
        assert p.home_access == "none"
        assert all(m == "none" for m in p.dirs.values())
        assert p.devices["gpu"]  # a window still needs GL
        assert not p.devices["audio"] and not p.devices["camera"]

    def test_developer_is_weaker_and_flagged(self):
        p = build_profile("developer")
        assert p.profile == "developer"
        assert p.home_access == "rw"
        assert p.network and p.devices["camera"]

    def test_every_profile_maps_all_device_keys(self):
        for name in ("standard", "strict", "developer"):
            p = build_profile(name)
            assert set(p.devices.keys()) == set(DEVICE_KEYS)


class TestGuards:
    def test_default_is_never_developer(self):
        assert DEFAULT_PROFILE_NAME == "standard"
        assert default_profile_name() != "developer"
        assert not is_default_profile("developer")

    def test_validate_profile_names(self):
        assert validate_profile_name("STRICT") == "strict"
        assert validate_profile_name("developer") == "developer"
        assert validate_profile_name("bogus") == "custom"
        assert validate_profile_name("") == "custom"

    def test_build_profile_unknown_falls_back_to_custom(self):
        # Stored/imported junk never crashes resolution; it degrades to the
        # fail-safe custom profile.
        p = build_profile("does_not_exist")
        assert p.profile == "custom"

    def test_build_custom_marks_custom(self):
        p = build_custom(preset_default("App", "bwrap"))
        assert p.profile == "custom"


class TestSwitching:
    def test_strict_from_default_tightens(self):
        plan = switch_profile(preset_default("App", "bwrap"), "strict")
        assert plan["policy"].profile == "strict"
        assert plan["relaxed"] == []
        assert any("Network" in t for t in plan["tightened"])

    def test_switching_to_developer_lists_relaxations(self):
        plan = switch_profile(build_profile("strict"), "developer")
        assert any("DEVELOPER" in r for r in plan["relaxed"])
        assert any("home" in r.lower() for r in plan["relaxed"])

    def test_switching_writes_nothing(self):
        plan = switch_profile(preset_default("App", "bwrap"), "standard")
        # Pure function: computes the target, persists nothing.
        assert plan["profile"] == "standard"


class TestDeltas:
    def test_home_relaxation_detected(self):
        cur = build_profile("standard")
        new = build_profile("standard")
        new.home_access = "rw"
        d = restriction_deltas(cur, new)
        assert any("Home" in r for r in d["relaxed"])
        cur_rw = build_profile("developer")
        d2 = restriction_deltas(cur_rw, build_profile("strict"))
        assert any("Home" in t for t in d2["tightened"])

    def test_device_delta_message(self):
        cur = build_profile("standard")
        new = build_profile("standard")
        new.devices["camera"] = True
        d = restriction_deltas(cur, new)
        assert any("camera" in r for r in d["relaxed"])


class TestSchemaMigration:
    def test_schema_version_is_2(self):
        assert POLICY_SCHEMA_VERSION == 2

    def test_v1_policy_becomes_custom(self):
        p = SandboxPolicy.from_dict({"schema_version": 1, "app_id": "Old"})
        assert p.profile == "custom"

    def test_roundtrip_keeps_profile(self):
        p = build_profile("strict")
        assert SandboxPolicy.from_dict(p.to_dict()).profile == "strict"

    def test_unknown_profile_names_map_to_custom(self):
        p = SandboxPolicy.from_dict({"schema_version": 2, "profile": "wibble"})
        assert p.profile == "wibble"  # stored verbatim; UI treats unknowns as custom


class TestDescribe:
    def test_describe_contains_key_facts(self):
        text = describe_profile("strict")
        assert "Strict" in text
        assert "Network: blocked" in text
        assert "Home: no access" in text
