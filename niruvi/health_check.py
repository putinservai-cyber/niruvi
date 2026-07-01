import datetime
import os
import shutil
import stat
import subprocess
import time

HEALTH_DAYS_THRESHOLD = 60


def check_single_appimage_mount(path: str) -> dict:
    """Check if an AppImage can be mounted (FUSE sanity check)."""
    result = {"mountable": False, "fuse_available": False, "error": ""}
    result["fuse_available"] = check_fuse_available()
    if not result["fuse_available"]:
        result["error"] = "FUSE is not available on this system"
        return result
    if not os.path.isfile(path):
        result["error"] = "File not found"
        return result
    if os.path.getsize(path) < 1024 * 1024:
        result["error"] = "File too small (< 1 MB)"
        return result
    try:
        st = os.stat(path)
        if not (st.st_mode & stat.S_IXUSR):
            result["error"] = "AppImage is not executable"
            return result
    except OSError as e:
        result["error"] = f"Cannot stat: {e}"
        return result
    result["mountable"] = True
    return result


def check_app_runnable(app_name: str, app_dir: str) -> dict:
    """Basic pre-launch check. Returns issues, warnings, info."""
    issues: list[str] = []
    warnings: list[str] = []
    info: dict = {}

    if not app_dir or not os.path.isdir(app_dir):
        issues.append("App directory does not exist")
        return {"app_name": app_name, "issues": issues, "warnings": warnings, "info": info, "healthy": False}

    apprun = os.path.join(app_dir, "AppRun")
    if not os.path.isfile(apprun):
        issues.append("AppRun not found in app directory")
    elif not os.access(apprun, os.X_OK):
        issues.append("AppRun is not executable")
    else:
        apprun_size = os.path.getsize(apprun)
        info["apprun_size"] = apprun_size
        if apprun_size > 100 * 1024 * 1024:
            warnings.append(f"AppRun is large ({apprun_size / 1024 / 1024:.0f} MB)")

        try:
            with open(apprun, "rb") as f:
                shebang = f.read(256)
            if shebang.startswith(b"#!"):
                interpreter_end = shebang.find(b"\n")
                interpreter_line = shebang[:interpreter_end].decode("utf-8", errors="replace")
                info["interpreter"] = interpreter_line
                interpreter_path = interpreter_line[2:].strip().split(" ")[0]
                if not os.path.isfile(interpreter_path):
                    issues.append(f"Interpreter not found: {interpreter_path}")
        except OSError as e:
            warnings.append(f"Cannot read AppRun: {e}")

    return {
        "app_name": app_name,
        "issues": issues,
        "warnings": warnings,
        "info": info,
        "healthy": len(issues) == 0,
    }


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
            info["no_update_url"] = True
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


def check_system_compatibility() -> dict:
    issues = []
    info = {}
    info["os"] = f"{os.uname().sysname} {os.uname().release}"
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    info["distro"] = line.split("=", 1)[1].strip().strip('"')
                    break
    except Exception:
        info["distro"] = "unknown"
    info["kernel"] = os.uname().version
    info["python"] = __import__("platform").python_version()
    try:
        r = subprocess.run(["rpm", "-q", "glibc", "--qf", "%{VERSION}"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            info["glibc"] = r.stdout.strip()
    except Exception:
        pass
    try:
        r = subprocess.run(["rpm", "-q", "mesa-dri-drivers", "--qf", "%{VERSION}"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            info["mesa"] = r.stdout.strip()
    except Exception:
        pass
    return {"issues": issues, "info": info, "healthy": len(issues) == 0}


def format_health_icon(h: dict) -> str:
    if not h["healthy"]:
        return "emblem-important"
    if h["warnings"]:
        return "emblem-warning"
    return "emblem-default"
