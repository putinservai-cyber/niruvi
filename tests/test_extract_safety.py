"""Tests for package-extraction path-traversal guards in niruvi.build.page."""

import os

from niruvi.build.page import SecurityError, _guard_extraction, _safe_member_names


def test_guard_extraction_allows_normal_tree(tmp_path):
    (tmp_path / "usr").mkdir()
    (tmp_path / "usr" / "bin").mkdir()
    f = tmp_path / "usr" / "bin" / "app"
    f.write_text("binary")
    # Must not raise for a clean extraction tree.
    _guard_extraction(str(tmp_path))


def test_guard_extraction_detects_symlink_escape(tmp_path):
    (tmp_path / "usr").mkdir()
    outside = tmp_path.parent / "escape_target"
    outside.write_text("pwned")
    link = tmp_path / "usr" / "evil"
    os.symlink(outside, link)
    # A symlink pointing outside dest resolves outside -> must be rejected.
    try:
        _guard_extraction(str(tmp_path))
    except SecurityError:
        outside.unlink(missing_ok=True)
        return
    outside.unlink(missing_ok=True)
    raise AssertionError("Expected SecurityError for symlink escape")


def test_guard_extraction_detects_absolute_symlink_escape(tmp_path):
    (tmp_path / "usr").mkdir()
    outside = tmp_path.parent / "escape_target"
    outside.write_text("pwned")
    # An absolute symlink (as if extracted from an absolute cpio member) escapes dest.
    link = tmp_path / "usr" / "evil"
    os.symlink(str(outside), link)
    try:
        _guard_extraction(str(tmp_path))
    except SecurityError:
        outside.unlink(missing_ok=True)
        return
    outside.unlink(missing_ok=True)
    raise AssertionError("Expected SecurityError for absolute symlink escape")


def test_safe_member_names_rejects_path_separators(tmp_path, monkeypatch):
    # A malicious .deb could carry an ar member named with a path separator.
    monkeypatch.setattr(os, "listdir", lambda _d: ["data.tar.zst", "../evil"])
    try:
        _safe_member_names(str(tmp_path))
    except SecurityError:
        return
    raise AssertionError("Expected SecurityError for unsafe ar member name")


def test_safe_member_names_allows_flat_names(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "listdir", lambda _d: ["data.tar.zst", "control.tar.zst", "debian-binary"])
    _safe_member_names(str(tmp_path))
