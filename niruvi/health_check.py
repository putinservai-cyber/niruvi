import datetime
import logging
import os
import subprocess
import time
from pathlib import Path

HEALTH_DAYS_THRESHOLD = 60


def check_app_health(app_name: str, app_dir: str, record) -> dict:
    issues = []
    warnings = []
    info = {}
    now = time.time()

    apprun_path = os.path.join(app_dir, "AppRun")
    if not os.path.isfile(apprun_path):
        issues.append("AppRun not found")
    elif not os.access(apprun_path, os.X_OK):
        issues.append("AppRun is not executable")
    else:
        info["apprun_size"] = os.path.getsize(apprun_path)

    if not os.path.isdir(app_dir):
        issues.append("App directory missing")
    else:
        stat_info = os.stat(app_dir)
        info["ctime"] = stat_info.st_ctime
        age_days = (now - stat_info.st_ctime) / 86400
        info["age_days"] = round(age_days, 1)
        if age_days > HEALTH_DAYS_THRESHOLD:
            issues.append(f"Last update was {int(age_days)} days ago (>{HEALTH_DAYS_THRESHOLD})")

    if record:
        install_date_str = getattr(record, "install_date", "")
        if install_date_str:
            try:
                install_dt = datetime.datetime.fromisoformat(install_date_str)
                install_age = (now - install_dt.timestamp()) / 86400
                if install_age > HEALTH_DAYS_THRESHOLD:
                    if "No updates checked" not in [x for x in warnings]:
                        warnings.append("No updates checked recently")
            except (ValueError, TypeError):
                pass
        if not getattr(record, "update_url", ""):
            warnings.append("No update URL configured")
        if getattr(record, "source_sha256", ""):
            info["sha256"] = record.source_sha256[:16]

    desktop = getattr(record, "desktop_file", "") or ""
    if desktop and not os.path.isfile(desktop):
        warnings.append("Desktop file missing")

    return {
        "app_name": app_name,
        "issues": issues,
        "warnings": warnings,
        "info": info,
        "healthy": len(issues) == 0,
    }


def check_fuse_available() -> bool:
    try:
        result = subprocess.run(
            ["fusermount3", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    try:
        result = subprocess.run(
            ["fusermount", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    try:
        result = subprocess.run(
            ["which", "fusermount3", "fusermount"],
            capture_output=True, text=True, timeout=5,
        )
        return bool(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def check_namespace_available() -> bool:
    try:
        result = subprocess.run(
            ["unshare", "--user", "--mount", "true"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def get_health_summary(installed_apps: dict, registry) -> dict:
    total = len(installed_apps)
    healthy = 0
    issues = 0
    warnings = 0
    results = {}
    for name, info in installed_apps.items():
        record = registry.get(name) if registry else None
        h = check_app_health(name, info.get("path", ""), record)
        results[name] = h
        if h["healthy"]:
            healthy += 1
        if h["issues"]:
            issues += 1
        if h["warnings"]:
            warnings += 1
    return {
        "total": total,
        "healthy": healthy,
        "issues": issues,
        "warnings": warnings,
        "results": results,
        "fuse_available": check_fuse_available(),
        "namespace_available": check_namespace_available(),
    }


def format_health_icon(h: dict) -> str:
    if not h["healthy"]:
        return "emblem-important"
    if h["warnings"]:
        return "emblem-warning"
    return "emblem-default"
