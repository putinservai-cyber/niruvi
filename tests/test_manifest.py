"""Tests for the Package Manifest module."""

import json
import os
import tempfile
import pytest

from niruvi.core.manifest import (
    Manifest, ManifestError, default_manifest, find_manifest, load_manifest,
    MANIFEST_FILENAME,
)


class TestManifestCreation:
    def test_default_manifest(self):
        m = default_manifest("com.example.app", "My App", "2.0.0")
        assert m.app_id == "com.example.app"
        assert m.app_name == "My App"
        assert m.version == "2.0.0"
        assert m.installer_type == "typical"
        assert m.update_url == ""

    def test_manifest_custom_values(self):
        data = {
            "app_id": "com.example.test",
            "app_name": "TestApp",
            "version": "1.5.0",
            "publisher": "Test Corp",
            "description": "A test app",
            "category": "Development",
            "update": {"url": "https://example.com/update.json", "channel": "beta"},
            "installer": {"type": "custom"},
        }
        m = Manifest(data)
        assert m.app_id == "com.example.test"
        assert m.version == "1.5.0"
        assert m.update_url == "https://example.com/update.json"
        assert m.installer_type == "custom"

    def test_manifest_with_partial_data(self):
        m = Manifest({"app_id": "test", "app_name": "Test", "version": "1.0"})
        assert m.app_id == "test"
        assert m.app_name == "Test"
        assert m.version == "1.0"

    def test_invalid_install_type(self):
        with pytest.raises(ManifestError, match="Invalid install type"):
            Manifest({
                "manifest_version": "1.0",
                "app_id": "test",
                "app_name": "test",
                "version": "1.0",
                "build": {},
                "installer": {"type": "invalid_type"},
            })

    def test_invalid_update_channel(self):
        with pytest.raises(ManifestError, match="Invalid update channel"):
            Manifest({
                "manifest_version": "1.0",
                "app_id": "test",
                "app_name": "test",
                "version": "1.0",
                "build": {},
                "update": {"channel": "unknown"},
            })

    def test_invalid_category(self):
        with pytest.raises(ManifestError, match="Invalid category"):
            Manifest({
                "manifest_version": "1.0",
                "app_id": "test",
                "app_name": "test",
                "version": "1.0",
                "build": {},
                "category": "NotARealCategory",
            })


class TestManifestSerialization:
    def test_to_json_roundtrip(self):
        m1 = default_manifest("com.example.app", "MyApp", "1.0.0",
                               publisher="Pub", description="Desc")
        json_str = m1.to_json()
        m2 = Manifest.from_json(json_str)
        assert m1 == m2
        assert m2.to_dict()["publisher"] == "Pub"
        assert m2.to_dict()["description"] == "Desc"

    def test_to_dict(self):
        m = default_manifest("com.example.app", "Test", "1.0")
        d = m.to_dict()
        assert isinstance(d, dict)
        assert d["app_id"] == "com.example.app"
        assert d["app_name"] == "Test"

    def test_file_roundtrip(self):
        m1 = default_manifest("com.example.file", "FileTest", "1.0.0")
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            tmp_path = f.name
        try:
            m1.to_file(tmp_path)
            m2 = Manifest.from_file(tmp_path)
            assert m1 == m2
        finally:
            os.unlink(tmp_path)


class TestManifestMerge:
    def test_merge_overrides(self):
        m = default_manifest("com.example.app", "Original", "1.0")
        m.merge({"version": "2.0", "publisher": "New Publisher"})
        assert m.version == "2.0"
        assert m.app_name == "Original"  # unchanged

    def test_deep_merge(self):
        m = default_manifest("com.example.app", "App", "1.0")
        m.merge({"update": {"url": "https://example.com/update.json", "channel": "nightly"}})
        assert m.update_url == "https://example.com/update.json"

    def test_merge_new_keys(self):
        m = default_manifest("com.example.app", "App", "1.0")
        m.merge({"file_associations": [{"ext": "txt", "name": "Text"}]})
        assert len(m.to_dict()["file_associations"]) == 1


class TestFindAndLoad:
    def test_find_manifest_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            assert find_manifest(tmpdir) is None

    def test_find_manifest_in_install_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            install_dir = os.path.join(tmpdir, ".niruvi-install")
            os.makedirs(install_dir)
            manifest_path = os.path.join(install_dir, MANIFEST_FILENAME)
            m = default_manifest("com.example.test", "Test", "1.0")
            m.to_file(manifest_path)
            found = find_manifest(tmpdir)
            assert found is not None
            assert found == manifest_path

    def test_load_manifest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            install_dir = os.path.join(tmpdir, ".niruvi-install")
            os.makedirs(install_dir)
            m1 = default_manifest("com.example.test", "Test", "1.0")
            m1.to_file(os.path.join(install_dir, MANIFEST_FILENAME))
            m2 = load_manifest(tmpdir)
            assert m2 is not None
            assert m2.app_id == "com.example.test"

    def test_load_manifest_corrupted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            install_dir = os.path.join(tmpdir, ".niruvi-install")
            os.makedirs(install_dir)
            with open(os.path.join(install_dir, MANIFEST_FILENAME), "w") as f:
                f.write("not json")
            assert load_manifest(tmpdir) is None


class TestManifestProperties:
    def test_repr(self):
        m = default_manifest("com.example.app", "MyApp", "3.0.0")
        r = repr(m)
        assert "Manifest" in r
        assert "com.example.app" in r
        assert "3.0.0" in r

    def test_equality(self):
        m1 = default_manifest("com.example.app", "App", "1.0")
        m2 = default_manifest("com.example.app", "App", "1.0")
        assert m1 == m2

    def test_inequality(self):
        m1 = default_manifest("com.example.app", "App", "1.0")
        m2 = default_manifest("com.example.app", "App", "2.0")
        assert m1 != m2
