import json
import logging
import os
import re
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse


GITHUB_API = "https://api.github.com/repos/{owner}/{repo}/releases/latest"
GITLAB_API = "https://gitlab.com/api/v4/projects/{project_id}/releases/permalink/latest"


@dataclass
class UpdateInfo:
    version: str
    download_url: str
    sha256: str | None = None
    changelog: str | None = None
    source_type: str = "direct"
    release_date: str | None = None


def detect_source_type(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host == "github.com" or host.endswith(".github.com"):
        return "github"
    elif host == "gitlab.com" or host.endswith(".gitlab.com"):
        return "gitlab"
    return "direct"


def parse_github_repo(url: str) -> tuple[str, str] | None:
    m = re.match(r'^(?:https?://)?(?:www\.)?github\.com/([^/]+)/([^/]+?)(?:\.git)?(?:\/|$)', url)
    if m:
        return m.group(1), m.group(2).rstrip("/")
    return None


def parse_gitlab_project(url: str) -> str | None:
    m = re.match(r'^(?:https?://)?(?:www\.)?gitlab\.com/(.+?)(?:\.git)?(?:\/|$)', url)
    if m:
        return m.group(1).rstrip("/")
    return None


def _fetch_json(url: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    resp = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(resp.read().decode("utf-8"))


def _get_arch_filter() -> str:
    import platform
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "x86_64"
    elif machine in ("aarch64", "arm64"):
        return "aarch64"
    elif machine in ("armv7l", "armhf"):
        return "armhf"
    elif machine in ("i386", "i686"):
        return "i386"
    return machine


def _score_asset(name: str, arch: str) -> int:
    name_lower = name.lower()
    score = 0
    if arch in name_lower:
        score += 10
    if ".appimage" in name_lower:
        score += 20
    if ".AppImage" in name:
        score += 5
    if "latest" in name_lower:
        score += 2
    if "-beta" in name_lower or "-alpha" in name_lower or "-nightly" in name_lower:
        score -= 5
    if "586" in name_lower or "686" in name_lower or "i386" in name_lower:
        score -= 3
    return score


def resolve_github(url: str, channel: str = "stable", timeout: int = 15) -> UpdateInfo | None:
    repo = parse_github_repo(url)
    if not repo:
        return None
    owner, repo_name = repo
    api_url = GITHUB_API.format(owner=owner, repo=repo_name)
    try:
        data = _fetch_json(api_url, timeout)
    except Exception as e:
        logging.debug("GitHub API error for %s: %s", url, e)
        return None

    tag = data.get("tag_name", "")
    version = tag.lstrip("vV")
    changelog = data.get("body", "")[:2000] if data.get("body") else None
    release_date = data.get("published_at", "")

    assets = data.get("assets", [])
    if not assets:
        return None

    arch = _get_arch_filter()
    best_asset = None
    best_score = -1

    for asset in assets:
        name = asset.get("name", "")
        score = _score_asset(name, arch)
        if score > best_score:
            best_score = score
            best_asset = asset

    if not best_asset:
        return None

    return UpdateInfo(
        version=version,
        download_url=best_asset["browser_download_url"],
        sha256=None,
        changelog=changelog,
        source_type="github",
        release_date=release_date,
    )


def resolve_gitlab(url: str, channel: str = "stable", timeout: int = 15) -> UpdateInfo | None:
    project_path = parse_gitlab_project(url)
    if not project_path:
        return None
    from urllib.parse import quote
    encoded = quote(project_path, safe="")
    api_url = GITLAB_API.format(project_id=encoded)
    try:
        data = _fetch_json(api_url, timeout)
    except Exception as e:
        logging.debug("GitLab API error for %s: %s", url, e)
        return None

    tag = data.get("tag_name", "")
    version = tag.lstrip("vV")
    changelog = data.get("description", "")[:2000] if data.get("description") else None
    release_date = data.get("created_at", "")

    assets = data.get("assets", {})
    links = assets.get("links", [])
    if not links:
        sources = assets.get("sources", [])
        for src in sources:
            fmt = src.get("format", "")
            url_val = src.get("url", "")
            if fmt == "tar.gz" and url_val:
                return UpdateInfo(
                    version=version,
                    download_url=url_val,
                    sha256=None,
                    changelog=changelog,
                    source_type="gitlab",
                    release_date=release_date,
                )
        return None

    arch = _get_arch_filter()
    best_link = None
    best_score = -1
    for link in links:
        name = link.get("name", "")
        score = _score_asset(name, arch)
        if score > best_score:
            best_score = score
            best_link = link

    if not best_link:
        return None

    return UpdateInfo(
        version=version,
        download_url=best_link["url"],
        sha256=None,
        changelog=changelog,
        source_type="gitlab",
        release_date=release_date,
    )


def resolve_direct(url: str, current_version: str = "", timeout: int = 15) -> UpdateInfo | None:
    try:
        req = urllib.request.Request(url, method="HEAD")
        resp = urllib.request.urlopen(req, timeout=timeout)
        headers = resp.headers
        content_disposition = headers.get("Content-Disposition", "")
        m = re.search(r'filename=["\']?([^"\';\n]+)', content_disposition)
        filename = m.group(1) if m else os.path.basename(urlparse(url).path)
        version = ""
        if filename:
            m2 = re.search(r'[vV]?(\d+\.\d+\.\d+[a-zA-Z0-9._-]*)', filename)
            if m2:
                version = m2.group(1)
        if not version and current_version:
            version = current_version
        return UpdateInfo(
            version=version or "unknown",
            download_url=url,
            sha256=None,
            source_type="direct",
        )
    except Exception as e:
        logging.debug("Direct URL HEAD failed for %s: %s", url, e)
        return None


def resolve_update_source(url: str, current_version: str = "",
                          channel: str = "stable", timeout: int = 15) -> UpdateInfo | None:
    source_type = detect_source_type(url)
    if source_type == "github":
        return resolve_github(url, channel, timeout)
    elif source_type == "gitlab":
        return resolve_gitlab(url, channel, timeout)
    else:
        return resolve_direct(url, current_version, timeout)


def parse_upd_info(appimage_path: str) -> UpdateInfo | None:
    try:
        with open(appimage_path, "rb") as f:
            data = f.read()
            idx = data.find(b".upd_info")
            if idx < 0:
                idx = data.find(b"update_info")
            if idx < 0:
                return None
            start = data.rfind(b"\x00", 0, idx)
            if start < 0:
                start = 0
            else:
                start += 1
            end = data.find(b"\x00", idx)
            if end < 0:
                end = min(idx + 512, len(data))
            segment = data[start:end]
            text = segment.decode("utf-8", errors="replace").strip().rstrip("\x00")
            urls = re.findall(r'https?://[^\s"\']+', text)
            if urls:
                return UpdateInfo(
                    version="",
                    download_url=urls[0],
                    sha256=None,
                    source_type="upd_info",
                )
    except Exception as e:
        logging.debug("Failed to parse .upd_info from %s: %s", appimage_path, e)
    return None


def normalize_update_url(url: str) -> str:
    url = url.strip()
    if url.startswith("github.com/"):
        url = "https://" + url
    elif url.startswith("gitlab.com/"):
        url = "https://" + url
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if url.endswith("/"):
        url = url.rstrip("/")
    for pattern in [".git", "/releases", "/releases/latest", "/releases/tag"]:
        if url.endswith(pattern) and "api.github" not in url:
            url = url[: -len(pattern)]
    return url


def guess_appimage_name(repo_url: str, app_name: str) -> str | None:
    repo = parse_github_repo(repo_url)
    if not repo:
        return None
    _, repo_name = repo
    arch = _get_arch_filter()
    candidates = [
        f"{app_name}-{arch}.AppImage",
        f"{app_name}-{arch}-latest.AppImage",
        f"{repo_name}-{arch}.AppImage",
        f"{app_name}.AppImage",
    ]
    return candidates[0] if candidates else None
