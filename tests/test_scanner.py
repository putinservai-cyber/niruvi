"""Tests for the safe extraction scanner module."""

import os
import tempfile

from niruvi.core.scanner import extract_safely


class TestExtractSafely:
    def test_invalid_path_returns_false(self):
        assert extract_safely("/nonexistent/file.AppImage", "/tmp/dest") is False

    def test_non_appimage_returns_false(self):
        with tempfile.NamedTemporaryFile(suffix=".AppImage", delete=False) as f:
            f.write(b"not an AppImage")
            path = f.name
        try:
            with tempfile.TemporaryDirectory() as dest:
                result = extract_safely(path, dest)
                assert result is False
        finally:
            os.unlink(path)

    def test_too_small_file_returns_false(self):
        with tempfile.NamedTemporaryFile(suffix=".AppImage", delete=False) as f:
            f.write(b"\x00" * 100)
            path = f.name
        try:
            with tempfile.TemporaryDirectory() as dest:
                result = extract_safely(path, dest)
                assert result is False
        finally:
            os.unlink(path)

    def test_extract_safely_no_unsquashfs_fallback(self, monkeypatch):
        monkeypatch.setattr("niruvi.core.scanner.shutil.which", lambda _: None)
        with tempfile.NamedTemporaryFile(suffix=".AppImage", delete=False) as f:
            f.write(b"\x00" * 1024 * 1024)
            path = f.name
        try:
            with tempfile.TemporaryDirectory() as dest:
                result = extract_safely(path, dest)
                assert result is False
        finally:
            os.unlink(path)
