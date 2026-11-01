"""Tests for app store discovery — catalog parsing, search, and fetching
with a mocked HTTP transport."""

import json
import urllib.error

import pytest

import niruvi.app.store as store
from niruvi.app.store import CatalogError, fetch_index, normalize_entry, parse_catalog, search


class TestNormalizeEntry:
    def test_aliases(self):
        entry = normalize_entry(
            {
                "title": "My App",
                "download_url": "http://x/y.AppImage",
                "summary": "does things",
                "tags": "utility",
            }
        )
        assert entry["name"] == "My App"
        assert entry["url"] == "http://x/y.AppImage"
        assert entry["categories"] == ["utility"]

    def test_missing_fields_returns_none(self):
        assert normalize_entry({"name": "x"}) is None
        assert normalize_entry({"url": "http://x"}) is None
        assert normalize_entry("not a dict") is None

    def test_canonical_keys(self):
        entry = normalize_entry(
            {
                "name": "A",
                "url": "u",
                "description": "d",
                "version": "1.0",
                "arch": "aarch64",
                "homepage": "h",
            }
        )
        assert entry == {
            "name": "A",
            "description": "d",
            "version": "1.0",
            "url": "u",
            "icon": "",
            "arch": "aarch64",
            "categories": [],
            "homepage": "h",
        }


class TestParseCatalog:
    def test_list_form(self):
        entries = parse_catalog([{"name": "A", "url": "u"}, {"name": "B", "url": "v"}])
        assert len(entries) == 2

    def test_dict_form(self):
        entries = parse_catalog({"apps": [{"name": "A", "url": "u"}]})
        assert entries[0]["name"] == "A"

    def test_invalid_entries_dropped(self):
        entries = parse_catalog([{"name": "A", "url": "u"}, {"name": "broken"}, 42])
        assert len(entries) == 1

    def test_not_a_catalog(self):
        with pytest.raises(CatalogError):
            parse_catalog({"apps": "nope"})
        with pytest.raises(CatalogError):
            parse_catalog(123)


class TestSearch:
    def _entries(self):
        return [
            {"name": "Blender", "description": "3D creation", "categories": ["graphics"], "arch": "x86_64", "url": "u"},
            {"name": "Spotify", "description": "Music", "categories": ["audio"], "arch": "any", "url": "v"},
            {
                "name": "Blender Arm",
                "description": "3D creation",
                "categories": ["graphics"],
                "arch": "aarch64",
                "url": "w",
            },
        ]

    def test_by_name(self):
        assert [e["name"] for e in search(self._entries(), "blender")] == ["Blender", "Blender Arm"]

    def test_by_description(self):
        assert [e["name"] for e in search(self._entries(), "music")] == ["Spotify"]

    def test_empty_query_returns_all(self):
        assert len(search(self._entries(), "")) == 3

    def test_arch_filter(self):
        assert [e["name"] for e in search(self._entries(), "", "aarch64")] == ["Spotify", "Blender Arm"]
        assert [e["name"] for e in search(self._entries(), "", "x86_64")] == ["Blender", "Spotify"]


class TestFetchIndex:
    def _fake_urlopen(self, payload, status=200):
        class _Resp:
            def read(self):
                return payload

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class _Opener:
            def __init__(self):
                self._resp = _Resp()

            def __call__(self, req, timeout=None, context=None):
                if status != 200:
                    raise urllib.error.HTTPError(req.full_url, status, "err", {}, None)
                return self._resp

        return _Opener()

    def test_fetch_ok(self, tmp_path, monkeypatch):
        catalog = json.dumps({"apps": [{"name": "A", "url": "http://x/y.AppImage"}]}).encode()
        monkeypatch.setattr(store.urllib.request, "urlopen", self._fake_urlopen(catalog))
        monkeypatch.setattr(store, "_cache_path", lambda: str(tmp_path / "catalog.json"))
        entries = fetch_index("http://example.org/catalog.json")
        assert entries[0]["name"] == "A"
        cached = store.load_cache()
        assert cached is not None
        assert cached[0][0]["name"] == "A"

    def test_fetch_http_error(self, monkeypatch):
        monkeypatch.setattr(store.urllib.request, "urlopen", self._fake_urlopen(b"", status=404))
        with pytest.raises(CatalogError, match="404"):
            fetch_index("http://example.org/catalog.json")

    def test_fetch_invalid_json(self, monkeypatch):
        monkeypatch.setattr(store.urllib.request, "urlopen", self._fake_urlopen(b"not json"))
        with pytest.raises(CatalogError, match="not valid JSON"):
            fetch_index("http://example.org/catalog.json")

    def test_fetch_network_error(self, monkeypatch):
        def fail(req, timeout=None, context=None):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(store.urllib.request, "urlopen", fail)
        with pytest.raises(CatalogError, match="network error"):
            fetch_index("http://example.org/catalog.json")

    def test_no_url_configured(self, monkeypatch):
        monkeypatch.setattr(store, "get_index_url", lambda: "")
        with pytest.raises(CatalogError, match="no store index URL"):
            fetch_index(None)

    def test_load_cache_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(store, "_cache_path", lambda: str(tmp_path / "nope.json"))
        assert store.load_cache() is None
