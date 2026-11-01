"""Tests for per-application policy storage (atomicity, history, import/export)."""

import json
import os

from niruvi.core.policy import FsGrant, SandboxPolicy
from niruvi.core.policy_store import PolicyStore, policy_mirror, resolve_policy


def _store(tmp_path) -> PolicyStore:
    return PolicyStore(str(tmp_path / "policies"))


class TestRoundTrip:
    def test_save_and_load(self, tmp_path):
        store = _store(tmp_path)
        p = SandboxPolicy(app_id="Demo", network=False)
        p.custom_paths = [FsGrant("/tmp/zniruvi-store-a", "rw")]
        assert store.save("Demo", p, note="init", changes=["network off"]) is True

        loaded = store.load("Demo")
        assert loaded.network is False
        assert loaded.app_id == "Demo"
        assert len(loaded.custom_paths) == 1

    def test_missing_returns_default(self, tmp_path):
        store = _store(tmp_path)
        p = store.load("Nope")
        assert p.app_id == "Nope"

    def test_delete(self, tmp_path):
        store = _store(tmp_path)
        store.save("Demo", SandboxPolicy(app_id="Demo"))
        assert store.delete("Demo") is True
        assert store.load_if_valid("Demo") is None


class TestHistory:
    def test_history_appended(self, tmp_path):
        store = _store(tmp_path)
        store.save("Demo", SandboxPolicy(app_id="Demo"), note="first", changes=["a"])
        store.save("Demo", SandboxPolicy(app_id="Demo"), note="second", changes=["b"])
        history = store.history("Demo")
        assert [h["note"] for h in history] == ["first", "second"]

    def test_history_bounded(self, tmp_path):
        from niruvi.core.policy_store import MAX_HISTORY

        store = _store(tmp_path)
        for i in range(MAX_HISTORY + 10):
            store.save("Demo", SandboxPolicy(app_id="Demo"), note=str(i), changes=[])
        assert len(store.history("Demo")) == MAX_HISTORY


class TestRecovery:
    def test_corrupt_file_quarantined(self, tmp_path):
        store = _store(tmp_path)
        store.save("Demo", SandboxPolicy(app_id="Demo"))
        path = os.path.join(store.dir(), "Demo.json")
        with open(path, "w") as f:
            f.write("{not valid json")
        # HMAC verification fails -> quarantined, default returned.
        loaded = store.load("Demo")
        assert loaded.app_id == "Demo"
        assert os.path.exists(path + ".invalid")

    def test_invalid_policy_not_saved(self, tmp_path):
        store = _store(tmp_path)
        p = SandboxPolicy(app_id="Demo")
        p.env = {"bad name": "x"}
        assert store.save("Demo", p) is False
        assert not store.exists("Demo")


class TestExportImport:
    def test_export_import_roundtrip(self, tmp_path):
        store = _store(tmp_path)
        p = SandboxPolicy(app_id="Demo", network=False, home_access="ro")
        store.save("Demo", p)

        data = store.export("Demo")
        assert data["niruvi_policy"] is True

        other = PolicyStore(str(tmp_path / "other"))
        imported = other.import_data(data, app_id="Demo")
        assert imported.network is False
        assert imported.home_access == "ro"
        assert other.exists("Demo")

    def test_import_rejects_non_policy(self, tmp_path):
        store = _store(tmp_path)
        try:
            store.import_data({"something": "else"})
            raise AssertionError("should have raised")
        except ValueError:
            pass

    def test_import_rejects_invalid(self, tmp_path):
        store = _store(tmp_path)
        bad = {
            "niruvi_policy": True,
            "app_id": "Demo",
            "policy": {"home_access": "sometimes"},
        }
        try:
            store.import_data(bad)
            raise AssertionError("should have raised")
        except ValueError:
            pass

    def test_export_to_file(self, tmp_path):
        store = _store(tmp_path)
        store.save("Demo", SandboxPolicy(app_id="Demo"))
        out = tmp_path / "demo.policy.json"
        assert store.export_to_file("Demo", str(out)) is True
        with open(out) as f:
            assert json.load(f)["niruvi_policy"] is True


class TestResolvePolicy:
    def test_store_wins(self, tmp_path):
        store = _store(tmp_path)
        store.save("Demo", SandboxPolicy(app_id="Demo", network=False))
        policy = resolve_policy("Demo", record=None, store=store)
        assert policy.network is False

    def test_record_without_enabled_stays_disabled(self, tmp_path):
        class Rec:
            sandbox_config = {}

        store = _store(tmp_path)
        policy = resolve_policy("Demo", record=Rec(), store=store)
        assert policy.enabled is False

    def test_record_mirror_used_when_no_store(self, tmp_path):
        class Rec:
            sandbox_config = {"enabled": True, "backend": "shield", "portable_home": True}

        store = _store(tmp_path)
        policy = resolve_policy("Demo", record=Rec(), store=store)
        assert policy.enabled is True
        assert policy.portable_home is True


class TestExternalModification:
    def test_stale_detected(self, tmp_path):
        store = _store(tmp_path)
        p = SandboxPolicy(app_id="Demo", network=False)
        store.save("Demo", p)

        class Rec:
            sandbox_config = {"enabled": True, "backend": "shield"}  # differs (no enable_network)

        assert store.mirror_is_stale("Demo", Rec()) is True

    def test_in_sync_not_stale(self, tmp_path):
        store = _store(tmp_path)
        p = SandboxPolicy(app_id="Demo")
        store.save("Demo", p)

        class Rec:
            sandbox_config = policy_mirror(p)

        assert store.mirror_is_stale("Demo", Rec()) is False
