"""Tests for the native system theme engine."""

import os
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from niruvi.utils.theme_engine import _NativeThemeEngine, ThemeMode, get_theme_engine


class TestNativeThemeEngine:
    """Tests for the native theme engine that uses system theme."""

    def test_mode_property(self):
        """Test that mode property works correctly."""
        engine = _NativeThemeEngine()
        assert engine.mode == ThemeMode.AUTO

        engine.mode = ThemeMode.LIGHT
        assert engine.mode == ThemeMode.LIGHT

        engine.mode = ThemeMode.DARK
        assert engine.mode == ThemeMode.DARK

    def test_stylesheet_is_empty(self):
        """Test that stylesheet returns empty string (no custom QSS)."""
        engine = _NativeThemeEngine()
        assert engine.stylesheet() == ""

    def test_palette_returns_system_palette(self):
        """Test that palette() returns system palette."""
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        engine = _NativeThemeEngine()
        palette = engine.palette()
        assert palette is not None
        assert isinstance(palette, app.palette().__class__)

    def test_apply_is_noop(self):
        """Test that apply() is a no-op."""
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        engine = _NativeThemeEngine()
        # Should not raise
        engine.apply(app)

    def test_prefers_reduced_motion(self):
        """Test that prefers_reduced_motion returns a boolean."""
        engine = _NativeThemeEngine()
        result = engine.prefers_reduced_motion()
        assert isinstance(result, bool)

    def test_global_get_theme_engine(self):
        """Test that get_theme_engine returns a singleton."""
        engine1 = get_theme_engine()
        engine2 = get_theme_engine()
        assert engine1 is engine2

    def test_color_constants(self):
        """Test that semantic color constants use palette roles."""
        from niruvi.utils.theme_engine import COLOR_SUCCESS, COLOR_WARNING, COLOR_ERROR, COLOR_INK
        assert COLOR_SUCCESS == "palette(highlight)"
        assert COLOR_WARNING == "palette(windowText)"
        assert COLOR_ERROR == "palette(windowText)"
        assert COLOR_INK == "palette(windowText)"


class TestThemeMode:
    """Tests for ThemeMode enum."""

    def test_modes_exist(self):
        assert ThemeMode.LIGHT
        assert ThemeMode.DARK
        assert ThemeMode.AUTO


class TestUrlTheme:
    """niruvi://install?theme= is ignored — we use native system theme.
    The theme parameter is kept for backward compatibility but has no effect."""

    def _engine(self, monkeypatch):
        engine = get_theme_engine()
        monkeypatch.setattr(engine, "_mode", engine.mode)
        return engine

    def test_dark_is_noop(self, monkeypatch):
        from niruvi.main import _apply_url_theme

        engine = self._engine(monkeypatch)
        before = engine.mode
        _apply_url_theme("dark")
        assert engine.mode == before  # No-op, uses system theme

    def test_light_is_noop(self, monkeypatch):
        from niruvi.main import _apply_url_theme

        engine = self._engine(monkeypatch)
        before = engine.mode
        _apply_url_theme("LIGHT")
        assert engine.mode == before  # No-op, uses system theme

    def test_empty_theme_is_noop(self, monkeypatch):
        from niruvi.main import _apply_url_theme

        engine = self._engine(monkeypatch)
        before = engine.mode
        _apply_url_theme("")
        assert engine.mode == before

    def test_unknown_theme_ignored(self, monkeypatch):
        from niruvi.main import _apply_url_theme

        engine = self._engine(monkeypatch)
        before = engine.mode
        _apply_url_theme("neon")
        assert engine.mode == before


class TestWebInstallRouting:
    """Regression: urlparse puts 'niruvi://ping' into netloc, not path — the
    ping check must not fall through to the install branch (which opens a
    blocking QMessageBox)."""

    @staticmethod
    def _normalized_path(url: str) -> str:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        path = parsed.path
        if not path and parsed.netloc:
            path = "/" + parsed.netloc
        return path

    def test_ping_canonical_form(self):
        assert self._normalized_path("niruvi://ping") == "/ping"

    def test_ping_with_query(self):
        assert self._normalized_path("niruvi://ping?theme=dark") == "/ping"

    def test_ping_slash_form(self):
        assert self._normalized_path("niruvi:///ping") == "/ping"

    def test_install_canonical_form(self):
        assert self._normalized_path("niruvi://install?url=https://x/y.AppImage") == "/install"

    def test_ping_returns_silently(self, monkeypatch):
        """handle_web_install must return before any QMessageBox on ping."""
        import importlib

        main_mod = importlib.import_module("niruvi.main")

        def _boom(*_a, **_k):
            raise AssertionError("QMessageBox shown for niruvi://ping")

        monkeypatch.setattr("PyQt6.QtWidgets.QMessageBox.critical", _boom)
        main_mod.handle_web_install("niruvi://ping")
        main_mod.handle_web_install("niruvi://ping?theme=dark")
        main_mod.handle_web_install("niruvi:///ping")


class TestReducedMotion:
    """Tests for reduced motion preference."""

    def test_probe_returns_boolean(self):
        engine = _NativeThemeEngine()
        result = engine.prefers_reduced_motion()
        assert isinstance(result, bool)