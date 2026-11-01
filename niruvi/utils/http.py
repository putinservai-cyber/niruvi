"""Shared HTTP utilities with SSL verification."""

import json
import logging
import ssl
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


def _create_ssl_context() -> ssl.SSLContext:
    """Create a verified SSL context. Falls back to CERT_REQUIRED."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def fetch_json(url: str, timeout: int = 15) -> dict:
    """Fetch JSON from a URL with SSL verification and timeout."""
    ctx = _create_ssl_context()
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        data: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
        return data
    except urllib.error.URLError as e:
        logger.warning("SSL/URL error fetching %s: %s", url, e)
        raise


def fetch_bytes(url: str, timeout: int = 30) -> bytes:
    """Fetch raw bytes from a URL with SSL verification."""
    ctx = _create_ssl_context()
    req = urllib.request.Request(url, headers={"Accept": "*/*"})
    try:
        resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        data: bytes = resp.read()
        return data
    except urllib.error.URLError as e:
        logger.warning("SSL/URL error fetching %s: %s", url, e)
        raise
