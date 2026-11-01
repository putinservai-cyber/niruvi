"""Tests for the Android-style PermissionsPage widget (App Info / Permissions).

Focus: capability-gated controls, apply/discard semantics, confirmation and
rollback on failure. These mirror the core promise: the UI never pretends a
control is enforced when the backend cannot enforce it.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QMessageBox

from niruvi.core.backends import ApplyResult, get_backend
from niruvi.core.policy_store import PolicyStore
from niruvi.ui.permissions_page import PermissionsPage

APP = {"version": "1.0", "name": "PermTestApp", "path": "/tmp/permtestapp"}


@pytest.fixture
def clean_store():
    store = PolicyStore()
    store.delete("PermTestApp")
    yield store
    store.delete("PermTestApp")


@pytest.fixture
def page(qapp, clean_store):
    from niruvi.utils.theme_engine import init_theme

    init_theme(qapp)
    widget = PermissionsPage("PermTestApp", dict(APP))
    yield widget
    widget.deleteLater()


class TestGating:
    def test_shield_devices_are_shown_as_not_enforced(self, page):
        if page._backend_combo.currentData() != "shield":
            pytest.skip("shield should be the fallback backend")
        entry = page._device_entries["camera"]
        assert entry.isEnabled() is False
        text = entry.state_label.text().lower()
        assert "not supported" in text or "not enforced" in text

    def test_bwrap_devices_are_supported(self, page):
        get_backend("bwrap").available() or pytest.skip("bwrap not available")
        page._policy.backend = "bwrap"
        page._original.backend = "bwrap"
        page._load_widget_state()
        page._refresh_gating()
        entry = page._device_entries["camera"]
        assert entry.isEnabled() is True
        assert "supported" in entry.state_label.text().lower()

    def test_master_off_shows_clear_status(self, page):
        page._master_toggle.setChecked(False)
        assert "isolation off" in page._enforcement_label.text().lower()

    def test_matches_capability_level_in_state_text(self, page):
        backend = page._backend()
        cap = backend.capability("ipc")
        entry = page._ipc_entries["wayland"]
        if cap.level == "unsupported":
            assert entry.isEnabled() is False
        else:
            assert entry.isEnabled() is True


class TestApplyFlow:
    def test_apply_persists_via_backend(self, page, monkeypatch):
        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Apply))
        page._network_toggle.setChecked(False)
        page._apply()
        from niruvi.core.policy_store import PolicyStore

        stored = PolicyStore().load("PermTestApp")
        assert stored.network is False
        assert stored.backend == page._backend_combo.currentData()

    def test_discard_reverts_unapplied_changes(self, page):
        page._home_combo.setCurrentIndex(1)  # Read-only
        assert page._collect().home_access == "ro"
        page._discard()
        assert page._collect().home_access == "rw"
        assert PolicyStore().load("PermTestApp").home_access == "rw"

    def test_rollback_invoked_when_apply_fails(self, page, monkeypatch):
        backend_class = type(page._backend())  # get_backend() builds a fresh instance
        called = {}

        def fail_apply(self, app_id, policy, record=None, store=None):
            called["applied"] = True
            return ApplyResult(False, "boom")

        def track_restore(self, app_id, previous, record=None, store=None):
            called["restored"] = previous.network
            return True

        monkeypatch.setattr(backend_class, "apply_policy", fail_apply)
        monkeypatch.setattr(backend_class, "restore_previous_policy", track_restore)
        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Apply))
        monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
        page._network_toggle.setChecked(False)
        page._apply()
        assert called.get("applied") is True
        assert called.get("restored") is True

    def test_apply_without_changes_noops(self, page, monkeypatch):
        store = PolicyStore()
        assert store.load("PermTestApp").network is True
        # Nothing changed, so Apply is disabled — no accidental write path.
        assert page._btn_apply.isEnabled() is False

    def test_reset_proposes_not_persists(self, page, monkeypatch):
        page._network_toggle.setChecked(False)
        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Apply))
        page._apply()
        page._reset_defaults()
        assert page._collect().network is True
        assert PolicyStore().load("PermTestApp").network is False  # nothing written until Apply


class TestCollect:
    def test_dir_modes_map_to_policy(self, page):
        for _key, combo in [("downloads", page._dir_combo_downloads)]:
            combo.setCurrentIndex(1)
        assert page._collect().dirs["downloads"] == "ro"

    def test_devices_toggle_flows_to_policy(self, page):
        page._device_toggles["camera"].setChecked(True)
        assert page._collect().devices["camera"] is True
        page._device_toggles["camera"].setChecked(False)
        assert page._collect().devices["camera"] is False
