"""App store discovery — fetch a remote AppImage catalog (index file).

The catalog is a JSON document, either a list of entries or
{"apps": [...]}. Each entry supports these fields (aliases accepted):

    name, description, version, url (download_url), icon, arch,
    categories (list), homepage

The index URL is configured in settings ("store_index_url"). Fetched
catalogs are cached locally in the data directory so the store still
works offline; a stale cache is used when the network fails.
"""

import json
import logging
import os
import time
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

DEFAULT_INDEX_URL = ""

_CACHE_FILE = "catalog.json"
_FETCH_TIMEOUT = 20


class CatalogError(Exception):
    pass


def get_index_url() -> str:
    try:
        from niruvi.config import get_settings

        return str(get_settings().get("store_index_url", "") or DEFAULT_INDEX_URL).strip()
    except Exception:
        return DEFAULT_INDEX_URL


def set_index_url(url: str) -> bool:
    try:
        from niruvi.config import get_settings, save_settings

        get_settings()["store_index_url"] = url.strip()
        save_settings()
        return True
    except Exception as e:
        logger.warning("Failed to save store index URL: %s", e)
        return False


def _cache_path() -> str:
    from niruvi.config import get_data_dir

    return os.path.join(get_data_dir(), _CACHE_FILE)


def _ssl_context():
    from niruvi.utils.http import _create_ssl_context as _ctx

    return _ctx()


def normalize_entry(raw: dict) -> dict | None:
    """Map arbitrary catalog entry fields to canonical keys."""
    if not isinstance(raw, dict):
        return None
    name = raw.get("name") or raw.get("title") or ""
    url = raw.get("url") or raw.get("download_url") or raw.get("appimage") or ""
    if not name or not url:
        return None
    categories = raw.get("categories") or raw.get("tags") or []
    if isinstance(categories, str):
        categories = [categories]
    return {
        "name": str(name).strip(),
        "description": str(raw.get("description") or raw.get("summary") or "").strip(),
        "version": str(raw.get("version") or "").strip(),
        "url": str(url).strip(),
        "icon": str(raw.get("icon") or "").strip(),
        "arch": str(raw.get("arch") or raw.get("architecture") or "x86_64").strip(),
        "categories": [str(c) for c in categories if c],
        "homepage": str(raw.get("homepage") or "").strip(),
    }


def parse_catalog(data: dict | list) -> list[dict]:
    if isinstance(data, list):
        raw_items = data
    elif isinstance(data, dict):
        raw_items = data.get("apps") or data.get("entries") or []
    else:
        raise CatalogError("catalog must be a list or an object with an 'apps' list")
    if not isinstance(raw_items, list):
        raise CatalogError("catalog is not a list of apps")
    entries = []
    for item in raw_items:
        entry = normalize_entry(item)
        if entry:
            entries.append(entry)
    return entries


def fetch_index(url: str | None = None, timeout: int = _FETCH_TIMEOUT) -> list[dict]:
    """Download and parse the catalog. Raises CatalogError on failure."""
    url = (url or get_index_url()).strip()
    if not url:
        raise CatalogError("no store index URL configured")
    req = urllib.request.Request(url, headers={"User-Agent": "Niruvi/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
            payload = resp.read()
        if not payload:
            raise CatalogError("empty catalog response")
        data = json.loads(payload)
        entries = parse_catalog(data)
        if not entries:
            raise CatalogError("catalog contains no apps")
        save_cache(entries, url)
        return entries
    except json.JSONDecodeError as e:
        raise CatalogError(f"catalog is not valid JSON: {e}") from e
    except urllib.error.HTTPError as e:
        raise CatalogError(f"HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise CatalogError(f"network error: {e}") from e


def save_cache(entries: list[dict], url: str):
    try:
        os.makedirs(os.path.dirname(_cache_path()), exist_ok=True)
        payload = {"url": url, "fetched": int(time.time()), "apps": entries}
        with open(_cache_path(), "w") as f:
            json.dump(payload, f)
    except OSError as e:
        logger.debug("Could not cache catalog: %s", e)


def load_cache() -> tuple[list[dict], str, int] | None:
    """Return (entries, url, fetched_ts) or None."""
    try:
        with open(_cache_path()) as f:
            data = json.load(f)
        entries = parse_catalog(data)
        return entries, str(data.get("url", "")), int(data.get("fetched", 0))
    except (OSError, json.JSONDecodeError, CatalogError, KeyError, ValueError) as e:
        logger.debug("No usable catalog cache: %s", e)
        return None


def search(entries: list[dict], query: str, arch: str = "") -> list[dict]:
    """Filter catalog entries by name/description/category and arch."""
    q = query.strip().lower()
    results = []
    for entry in entries:
        if arch and entry.get("arch", "x86_64") not in ("any", arch):
            continue
        if q:
            haystack = " ".join(
                [
                    entry.get("name", ""),
                    entry.get("description", ""),
                    " ".join(entry.get("categories", [])),
                ]
            ).lower()
            if q not in haystack:
                continue
        results.append(entry)
    return results
