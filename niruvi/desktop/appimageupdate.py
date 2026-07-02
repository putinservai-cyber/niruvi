"""AppImageUpdate (zsync) backend for delta updates.

Integrates with appimageupdatetool and AppImageUpdate to perform
delta updates of AppImages instead of full binary downloads.
"""

import logging
import os
import subprocess
import shutil

logger = logging.getLogger(__name__)


def check_appimageupdate_available() -> dict:
    """Check if AppImageUpdate/appimageupdatetool is available."""
    result = {"available": False, "tool": None, "path": None, "version": None}
    candidates = ["appimageupdatetool", "AppImageUpdate"]
    for tool in candidates:
        path = shutil.which(tool)
        if path:
            result["available"] = True
            result["tool"] = tool
            result["path"] = path
            try:
                r = subprocess.run(
                    [path, "--version"], capture_output=True, text=True, timeout=10,
                )
                result["version"] = (r.stdout or r.stderr or "").strip()[:80]
            except Exception:
                pass
            break
    return result


def get_update_info_from_appimage(appimage_path: str) -> str | None:
    """Extract update information from an AppImage via --appimage-updateinformation."""
    try:
        r = subprocess.run(
            [appimage_path, "--appimage-updateinformation"],
            capture_output=True, text=True, timeout=10,
        )
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        for text in (out, err):
            if text and not text.startswith("Warning"):
                return text
    except Exception:
        pass
    return None


def update_appimage_via_tool(appimage_path: str) -> tuple[bool, str]:
    """Update an AppImage in-place using the available update tool.

    Returns (success: bool, message: str).
    """
    info = check_appimageupdate_available()
    if not info["available"]:
        return False, "No update tool available"

    if not os.path.isfile(appimage_path):
        return False, f"AppImage not found: {appimage_path}"

    if not os.access(appimage_path, os.X_OK):
        return False, "AppImage is not executable"

    tool = info["path"]

    try:
        r = subprocess.run(
            [tool, appimage_path],
            capture_output=True, text=True, timeout=300,
        )
        if r.returncode == 0:
            msg = (r.stdout or "").strip() or "Update applied"
            return True, msg
        else:
            stderr = (r.stderr or "").strip()
            stdout = (r.stdout or "").strip()
            detail = stderr or stdout or f"Exit code {r.returncode}"
            # Not all failures mean no update available — might be up-to-date
            if "already up" in detail.lower() or "no update" in detail.lower():
                return True, "Already up-to-date"
            return False, detail
    except subprocess.TimeoutExpired:
        return False, "Update timed out (5 min)"
    except Exception as e:
        return False, str(e)


def find_appimage_in_dir(app_dir: str) -> str | None:
    """Find the original .AppImage file inside an installation directory."""
    if not os.path.isdir(app_dir):
        return None
    for f in os.listdir(app_dir):
        lower = f.lower()
        if lower.endswith(".appimage"):
            full = os.path.join(app_dir, f)
            if os.path.isfile(full):
                return full
    return None


def get_update_method_for_app(app_dir: str) -> dict:
    """Determine which update method to use for an installed app.

    Returns dict with:
      method: "zsync" | "download" | "none"
      appimage_path: str | None  (path to original AppImage if found)
      update_info: str | None    (embedded update info if found)
      tool_available: bool
    """
    result = {
        "method": "none",
        "appimage_path": None,
        "update_info": None,
        "tool_available": False,
    }

    appimage_path = find_appimage_in_dir(app_dir)
    if not appimage_path:
        return result

    result["appimage_path"] = appimage_path
    update_info = get_update_info_from_appimage(appimage_path)
    if update_info:
        result["update_info"] = update_info

    tool_info = check_appimageupdate_available()
    result["tool_available"] = tool_info["available"]

    if result["tool_available"] and result["update_info"]:
        result["method"] = "zsync"
    elif result["update_info"]:
        result["method"] = "download"
    else:
        result["method"] = "download"

    return result
