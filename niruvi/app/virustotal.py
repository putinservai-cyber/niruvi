"""VirusTotal integration — opt-in cloud scanning of AppImages before install.

The API key is stored in settings.json (HMAC-signed, mode 0600). All requests
use urllib (no extra dependencies) and fail gracefully: any network/API error
results in a "skipped" verdict rather than blocking installation.
"""

import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

API_BASE = "https://www.virustotal.com/api/v3"
REPORT_BASE = "https://www.virustotal.com/gui/file"

#: Free-tier limits: ~4 requests/minute, 500/day. Poll slowly to stay inside.
POLL_INTERVAL = 15
POLL_TIMEOUT = 300


class VirusTotalError(Exception):
    pass


def get_api_key() -> str:
    try:
        from niruvi.config import get_settings

        return str(get_settings().get("virustotal_api_key", "")).strip()
    except Exception:
        return ""


def set_api_key(key: str) -> bool:
    try:
        from niruvi.config import get_settings, save_settings

        settings = get_settings()
        settings["virustotal_api_key"] = key.strip()
        save_settings()
        return True
    except Exception as e:
        logger.warning("Failed to save VirusTotal API key: %s", e)
        return False


def _request(
    method: str, path: str, api_key: str, body: bytes | None = None, content_type: str = "", timeout: int = 60
) -> dict | None:
    url = f"{API_BASE}/{path}"
    headers = {"x-apikey": api_key}
    data = None
    if body is not None:
        data = body
        headers["content-type"] = content_type
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
            return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise VirusTotalError("rate limit exceeded") from e
        if e.code == 401:
            raise VirusTotalError("invalid API key") from e
        raise VirusTotalError(f"HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise VirusTotalError(f"network error: {e}") from e


def test_key(api_key: str) -> bool:
    """Verify the key by requesting a nonexistent file (401 = invalid)."""
    try:
        _request("GET", "files/" + "0" * 64, api_key, timeout=20)
        return True
    except VirusTotalError as e:
        return not ("invalid API key" in str(e) or "HTTP 401" in str(e))


def submit_file(path: str, api_key: str) -> str:
    """Upload a file and return its analysis id."""
    with open(path, "rb") as f:
        payload = f.read()
    boundary = "niruvi" + hashlib.sha1(os.urandom(16)).hexdigest()
    parts: list[bytes] = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        f'filename="{os.path.basename(path)}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode(),
        payload,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    body = parts[0] + parts[1] + parts[2]
    data = _request(
        "POST", "files", api_key, body=body, content_type=f"multipart/form-data; boundary={boundary}", timeout=180
    )
    if not data or not data.get("data", {}).get("id"):
        raise VirusTotalError("no analysis id returned")
    return str(data["data"]["id"])


def poll_analysis(analysis_id: str, api_key: str) -> dict:
    """Wait for the analysis to finish; returns the full analysis payload."""
    deadline = time.time() + POLL_TIMEOUT
    last: dict[str, Any] = {}
    while time.time() < deadline:
        data = _request("GET", f"analyses/{analysis_id}", api_key, timeout=30)
        if data and data.get("data", {}).get("attributes", {}).get("status") == "completed":
            return data
        last = data or last
        time.sleep(POLL_INTERVAL)
    raise VirusTotalError("analysis timed out")


def scan_file(path: str, api_key: str | None = None) -> dict:
    """Submit + poll. Returns a normalized result dict:
    {status, malicious, suspicious, harmless, undetected, sha256, permalink}
    status: 'clean' | 'flagged' | 'skipped' (on any error).
    """
    api_key = api_key or get_api_key()
    result: dict[str, Any] = {
        "status": "skipped",
        "malicious": 0,
        "suspicious": 0,
        "harmless": 0,
        "undetected": 0,
        "sha256": "",
        "permalink": "",
        "error": "",
    }
    if not api_key:
        result["error"] = "no API key configured"
        return result
    try:
        sha = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                sha.update(chunk)
        result["sha256"] = sha.hexdigest()
        result["permalink"] = f"{REPORT_BASE}/{result['sha256']}"
        analysis_id = submit_file(path, api_key)
        data = poll_analysis(analysis_id, api_key)
        attrs = data.get("data", {}).get("attributes", {})
        stats = attrs.get("stats", {})
        result["malicious"] = int(stats.get("malicious", 0))
        result["suspicious"] = int(stats.get("suspicious", 0))
        result["harmless"] = int(stats.get("harmless", 0))
        result["undetected"] = int(stats.get("undetected", 0))
        result["status"] = "flagged" if result["malicious"] > 0 else "clean"
    except Exception as e:
        logger.warning("VirusTotal scan failed for %s: %s", path, e)
        result["error"] = str(e)
    return result


def scan_known_hash(sha256: str, api_key: str) -> dict:
    """Look up an already-known file by hash (no upload needed)."""
    result: dict[str, Any] = {
        "status": "skipped",
        "malicious": 0,
        "suspicious": 0,
        "harmless": 0,
        "undetected": 0,
        "sha256": sha256,
        "permalink": f"{REPORT_BASE}/{sha256}",
        "error": "",
    }
    try:
        data = _request("GET", f"files/{sha256}", api_key, timeout=30)
        if not data:
            raise VirusTotalError("no response from VirusTotal")
        attrs = data.get("data", {}).get("attributes", {})
        stats = attrs.get("last_analysis_stats", {})
        result["malicious"] = int(stats.get("malicious", 0))
        result["suspicious"] = int(stats.get("suspicious", 0))
        result["harmless"] = int(stats.get("harmless", 0))
        result["undetected"] = int(stats.get("undetected", 0))
        result["status"] = "flagged" if result["malicious"] > 0 else "clean"
    except Exception as e:
        logger.warning("VirusTotal lookup failed for %s: %s", sha256, e)
        result["error"] = str(e)
    return result
