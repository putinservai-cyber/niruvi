"""Tests for uninstall cleanup helpers (MIME associations, icons)."""

from pathlib import Path

from niruvi.desktop.desktop_utils import _sanitize_icon_name, unregister_mime_handler
from niruvi.ui.uninstall_dialog import UninstallWorker


def _mimeapps_path(home: Path) -> Path:
    return home / ".config" / "mimeapps.list"


def _write_mimeapps(home: Path, content: str) -> Path:
    path = _mimeapps_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


class TestUnregisterMimeHandler:
    def test_missing_file_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        assert unregister_mime_handler("My App") is False

    def test_noop_when_absent_keeps_file_unchanged(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        content = "[Default Applications]\ntext/plain=other.desktop;\n"
        path = _write_mimeapps(tmp_path, content)
        assert unregister_mime_handler("My App") is False
        assert path.read_text() == content

    def test_removes_default_and_associations_only_for_us(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        # Use sanitized name (My-App) since that's what the function uses
        _write_mimeapps(
            tmp_path,
            "[Default Applications]\n"
            "text/plain=My-App.desktop;other.desktop;\n"
            "image/png=My-App.desktop;\n"
            "\n"
            "[Added Associations]\n"
            "text/plain=My-App.desktop;other.desktop;\n"
            "[Removed Associations]\n"
            "image/png=My-App.desktop;\n",
        )
        assert unregister_mime_handler("My App") is True
        data = _mimeapps_path(tmp_path).read_text()
        assert "My-App.desktop" not in data
        assert "other.desktop" in data
        assert "text/plain=other.desktop;" in data

    def test_drops_key_when_we_were_the_only_entry(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_mimeapps(tmp_path, "[Default Applications]\nimage/png=My-App.desktop;\n")
        assert unregister_mime_handler("My App") is True
        data = _mimeapps_path(tmp_path).read_text()
        assert "image/png" not in data

    def test_ignores_other_sections(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        content = "[Default Applications]\nimage/png=My-App.desktop;\n[Custom]\nfoo=My-App.desktop;\n"
        path = _write_mimeapps(tmp_path, content)
        assert unregister_mime_handler("My App") is True
        data = path.read_text()
        assert "[Custom]" in data
        assert "foo=My-App.desktop;" in data

    def test_explicit_desktop_name_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_mimeapps(tmp_path, "[Default Applications]\nimage/png=Custom.desktop;\n")
        assert unregister_mime_handler("My App", desktop_name="Custom") is True
        assert "Custom.desktop" not in _mimeapps_path(tmp_path).read_text()


class TestUninstallWorkerIcons:
    def test_removes_only_sanitized_icon_match(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        icons = tmp_path / ".local" / "share" / "icons" / "hicolor" / "256x256" / "apps"
        icons.mkdir(parents=True)
        ours = icons / f"{_sanitize_icon_name('My App')}.png"
        unrelated = icons / "my-app-extra.png"
        other = icons / "other.png"
        for f in (ours, unrelated, other):
            f.write_text("x")

        worker = UninstallWorker("My App", str(tmp_path / "app"), {"icons": True})
        worker.run()

        assert not ours.exists()
        assert unrelated.exists()
        assert other.exists()
        assert worker.warnings == []

    def test_thumbnails_never_use_substring_match(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        thumbs = tmp_path / ".cache" / "thumbnails" / "normal"
        thumbs.mkdir(parents=True)
        unrelated = thumbs / "unrelated-my-app-thumb.png"
        unrelated.write_text("x")

        worker = UninstallWorker("My App", str(tmp_path / "app"), {"thumbnails": True})
        worker.run()

        assert unrelated.exists()
        assert worker.warnings == []
