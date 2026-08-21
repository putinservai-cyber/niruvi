"""Tests for the cross-desktop theme detection and custom color overrides."""

import subprocess
from unittest.mock import MagicMock

from niruvi.utils.theme_engine import ThemeEngine, ThemeMode


def _fake_run(returncode=0, stdout="", stderr=""):
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


class TestPortalDetection:
    def test_portal_reports_dark(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_run(0, "(<uint32 1>,)\n"))
        assert ThemeEngine._probe_xdg_portal() == ThemeMode.DARK

    def test_portal_reports_light(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_run(0, "(<uint32 2>,)\n"))
        assert ThemeEngine._probe_xdg_portal() == ThemeMode.LIGHT

    def test_portal_no_preference_falls_through(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_run(0, "(<uint32 0>,)\n"))
        assert ThemeEngine._probe_xdg_portal() is None

    def test_portal_missing_binary_falls_through(self, monkeypatch):
        def _raise(*a, **k):
            raise FileNotFoundError("gdbus not found")

        monkeypatch.setattr(subprocess, "run", _raise)
        assert ThemeEngine._probe_xdg_portal() is None

    def test_portal_nonzero_exit_falls_through(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_run(1, ""))
        assert ThemeEngine._probe_xdg_portal() is None


class TestXfconfDetection:
    def test_xfconf_dark_theme_name(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_run(0, "Greybird-dark\n"))
        assert ThemeEngine._probe_xfconf() == ThemeMode.DARK

    def test_xfconf_light_theme_name(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_run(0, "Greybird\n"))
        assert ThemeEngine._probe_xfconf() == ThemeMode.LIGHT

    def test_xfconf_unavailable(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_run(1, ""))
        assert ThemeEngine._probe_xfconf() is None


class TestDetectionFallbackOrder:
    def test_portal_wins_over_gsettings(self, monkeypatch):
        engine = ThemeEngine()
        monkeypatch.setattr(engine, "_probe_xdg_portal", lambda: ThemeMode.DARK)
        monkeypatch.setattr(engine, "_probe_gsettings", lambda: ThemeMode.LIGHT)
        monkeypatch.setattr(engine, "_probe_kreadconfig", lambda: ThemeMode.LIGHT)
        monkeypatch.setattr(engine, "_probe_xfconf", lambda: ThemeMode.LIGHT)
        assert engine._detect_system_theme() == ThemeMode.DARK

    def test_falls_back_through_chain(self, monkeypatch):
        engine = ThemeEngine()
        monkeypatch.setattr(engine, "_probe_xdg_portal", lambda: None)
        monkeypatch.setattr(engine, "_probe_gsettings", lambda: None)
        monkeypatch.setattr(engine, "_probe_kreadconfig", lambda: None)
        monkeypatch.setattr(engine, "_probe_xfconf", lambda: ThemeMode.DARK)
        assert engine._detect_system_theme() == ThemeMode.DARK

    def test_defaults_to_light_when_nothing_detected(self, monkeypatch):
        engine = ThemeEngine()
        monkeypatch.setattr(engine, "_probe_xdg_portal", lambda: None)
        monkeypatch.setattr(engine, "_probe_gsettings", lambda: None)
        monkeypatch.setattr(engine, "_probe_kreadconfig", lambda: None)
        monkeypatch.setattr(engine, "_probe_xfconf", lambda: None)
        assert engine._detect_system_theme() == ThemeMode.LIGHT

    def test_result_is_cached(self, monkeypatch):
        engine = ThemeEngine()
        calls = {"n": 0}

        def _portal():
            calls["n"] += 1
            return ThemeMode.DARK

        monkeypatch.setattr(engine, "_probe_xdg_portal", _portal)
        engine._detect_system_theme()
        engine._detect_system_theme()
        assert calls["n"] == 1


class TestCustomColors:
    def test_valid_override_is_kept(self):
        engine = ThemeEngine()
        engine.set_custom_colors({"Highlight": "#7C3AED"})
        assert engine._custom_colors == {"Highlight": "#7C3AED"}

    def test_unknown_role_is_dropped(self):
        engine = ThemeEngine()
        engine.set_custom_colors({"NotARealRole": "#7C3AED"})
        assert engine._custom_colors == {}

    def test_invalid_hex_is_dropped(self):
        engine = ThemeEngine()
        engine.set_custom_colors({"Highlight": "not-a-color"})
        assert engine._custom_colors == {}

    def test_clear_custom_colors(self):
        engine = ThemeEngine()
        engine.set_custom_colors({"Highlight": "#7C3AED"})
        engine.clear_custom_colors()
        assert engine._custom_colors == {}

    def test_mixed_valid_and_invalid(self):
        engine = ThemeEngine()
        engine.set_custom_colors({"Highlight": "#7C3AED", "Bogus": "#111111", "Text": "nope"})
        assert engine._custom_colors == {"Highlight": "#7C3AED"}
