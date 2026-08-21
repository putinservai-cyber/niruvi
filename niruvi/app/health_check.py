import datetime
import logging
import os
import platform
import shutil
import stat
import subprocess
import time
from typing import Any

logger = logging.getLogger(__name__)

HEALTH_DAYS_THRESHOLD = 60

_fuse_available: bool | None = None
_namespace_available: bool | None = None

# Common AppImage shared libraries -> per-package-manager package names.
# soname prefixes mapped to {dnf, apt-get, pacman, zypper, apk} package names.
_COMMON_LIB_PACKAGES = {
    "libfuse.so.2": {"dnf": "fuse2", "apt-get": "libfuse2", "pacman": "fuse2", "zypper": "fuse2", "apk": "fuse"},
    "libnss3.so": {"dnf": "nss", "apt-get": "libnss3", "pacman": "nss", "zypper": "nss", "apk": "nss"},
    "libnssutil3.so": {
        "dnf": "nss-util",
        "apt-get": "libnss3",
        "pacman": "nss",
        "zypper": "nss-util",
        "apk": "nss-util",
    },
    "libX11.so.6": {"dnf": "libX11", "apt-get": "libx11-6", "pacman": "libx11", "zypper": "libX11-6", "apk": "libx11"},
    "libXext.so.6": {
        "dnf": "libXext",
        "apt-get": "libxext6",
        "pacman": "libxext",
        "zypper": "libXext6",
        "apk": "libxext",
    },
    "libxcb.so.1": {"dnf": "libxcb", "apt-get": "libxcb1", "pacman": "libxcb", "zypper": "libxcb1", "apk": "libxcb"},
    "libGL.so.1": {"dnf": "libGL", "apt-get": "libgl1", "pacman": "libgl", "zypper": "Mesa-libGL1", "apk": "mesa-gl"},
    "libEGL.so.1": {
        "dnf": "libEGL",
        "apt-get": "libegl1",
        "pacman": "libegl",
        "zypper": "Mesa-libEGL1",
        "apk": "mesa-egl",
    },
    "libgtk-3.so.0": {"dnf": "gtk3", "apt-get": "libgtk-3-0", "pacman": "gtk3", "zypper": "gtk3", "apk": "gtk+3.0"},
    "libgtk-4.so.1": {"dnf": "gtk4", "apt-get": "libgtk-4-1", "pacman": "gtk4", "zypper": "gtk4", "apk": "gtk4"},
    "libgdk_pixbuf-2.0.so.0": {
        "dnf": "gdk-pixbuf2",
        "apt-get": "libgdk-pixbuf-2.0-0",
        "pacman": "gdk-pixbuf2",
        "zypper": "gdk-pixbuf",
        "apk": "gdk-pixbuf",
    },
    "libcairo.so.2": {"dnf": "cairo", "apt-get": "libcairo2", "pacman": "cairo", "zypper": "cairo", "apk": "cairo"},
    "libpango-1.0.so.0": {
        "dnf": "pango",
        "apt-get": "libpango-1.0-0",
        "pacman": "pango",
        "zypper": "pango",
        "apk": "pango",
    },
    "libasound.so.2": {
        "dnf": "alsa-lib",
        "apt-get": "libasound2",
        "pacman": "alsa-lib",
        "zypper": "alsa-lib",
        "apk": "alsa-lib",
    },
    "libpulse.so.0": {
        "dnf": "pulseaudio-libs",
        "apt-get": "libpulse0",
        "pacman": "libpulse",
        "zypper": "libpulse0",
        "apk": "libpulse",
    },
    "libGLU.so.1": {"dnf": "libGLU", "apt-get": "libglu1-mesa", "pacman": "glu", "zypper": "libGLU1", "apk": "glu"},
    "libSM.so.6": {"dnf": "libSM", "apt-get": "libsm6", "pacman": "libsm", "zypper": "libSM6", "apk": "libsm"},
    "libICE.so.6": {"dnf": "libICE", "apt-get": "libice6", "pacman": "libice", "zypper": "libICE6", "apk": "libice"},
    "libXinerama.so.1": {
        "dnf": "libXinerama",
        "apt-get": "libxinerama1",
        "pacman": "libxinerama",
        "zypper": "libXinerama1",
        "apk": "libxinerama",
    },
    "libXrandr.so.2": {
        "dnf": "libXrandr",
        "apt-get": "libxrandr2",
        "pacman": "libxrandr",
        "zypper": "libXrandr2",
        "apk": "libxrandr",
    },
    "libXcursor.so.1": {
        "dnf": "libXcursor",
        "apt-get": "libxcursor1",
        "pacman": "libxcursor",
        "zypper": "libXcursor1",
        "apk": "libxcursor",
    },
    "libXfixes.so.3": {
        "dnf": "libXfixes",
        "apt-get": "libxfixes3",
        "pacman": "libxfixes",
        "zypper": "libXfixes3",
        "apk": "libxfixes",
    },
    "libXi.so.6": {"dnf": "libXi", "apt-get": "libxi6", "pacman": "libxi", "zypper": "libXi6", "apk": "libxi"},
    "libXtst.so.6": {
        "dnf": "libXtst",
        "apt-get": "libxtst6",
        "pacman": "libxtst",
        "zypper": "libXtst6",
        "apk": "libxtst",
    },
    "libsecret-1.so.0": {
        "dnf": "libsecret",
        "apt-get": "libsecret-1-0",
        "pacman": "libsecret",
        "zypper": "libsecret-1-0",
        "apk": "libsecret",
    },
    "libgconf-2.so.4": {
        "dnf": "GConf2",
        "apt-get": "libgconf-2-4",
        "pacman": "gconf",
        "zypper": "gconf2",
        "apk": "gconf",
    },
    "libssl.so.3": {
        "dnf": "openssl-libs",
        "apt-get": "libssl3",
        "pacman": "openssl",
        "zypper": "libopenssl3",
        "apk": "openssl",
    },
    "libcrypto.so.3": {
        "dnf": "openssl-libs",
        "apt-get": "libssl3",
        "pacman": "openssl",
        "zypper": "libopenssl3",
        "apk": "openssl",
    },
    "libcurl.so.4": {"dnf": "libcurl", "apt-get": "libcurl4", "pacman": "curl", "zypper": "libcurl4", "apk": "curl"},
    "libdbus-1.so.3": {
        "dnf": "dbus-libs",
        "apt-get": "libdbus-1-3",
        "pacman": "dbus",
        "zypper": "dbus-1",
        "apk": "dbus-libs",
    },
    "libatk-1.0.so.0": {
        "dnf": "atk",
        "apt-get": "libatk1.0-0",
        "pacman": "at-spi2-core",
        "zypper": "atk",
        "apk": "at-spi2-core",
    },
    "libgdk-3.so.0": {"dnf": "gtk3", "apt-get": "libgtk-3-0", "pacman": "gtk3", "zypper": "gtk3", "apk": "gtk+3.0"},
    "libvulkan.so.1": {
        "dnf": "vulkan-loader",
        "apt-get": "libvulkan1",
        "pacman": "vulkan-icd-loader",
        "zypper": "libvulkan1",
        "apk": "vulkan-loader",
    },
    "libva.so.2": {"dnf": "libva", "apt-get": "libva2", "pacman": "libva", "zypper": "libva2", "apk": "libva"},
    "libappindicator3.so.1": {
        "dnf": "libappindicator-gtk3",
        "apt-get": "libappindicator3-1",
        "pacman": "libappindicator-gtk3",
        "zypper": "libappindicator3",
        "apk": "libayatana-appindicator",
    },
}

_INSTALL_CMD = {
    "dnf": "sudo dnf install -y",
    "apt-get": "sudo apt install -y",
    "pacman": "sudo pacman -S --needed",
    "zypper": "sudo zypper install -y",
    "apk": "sudo apk add",
}

_pkg_manager: str | None = None


def detect_package_manager() -> str | None:
    """Detect the system package manager once."""
    global _pkg_manager
    if _pkg_manager is not None:
        return _pkg_manager
    for pm in ("dnf", "apt-get", "pacman", "zypper", "apk"):
        if shutil.which(pm):
            _pkg_manager = pm
            return pm
    _pkg_manager = ""
    return None


def _is_elf(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"\x7fELF"
    except OSError:
        return False


def _find_elf_target(app_dir: str) -> str | None:
    """Find the main ELF executable to run ldd against."""
    apprun = os.path.join(app_dir, "AppRun")
    if os.path.isfile(apprun) and _is_elf(apprun):
        return apprun
    try:
        entries = sorted(os.listdir(app_dir))
    except OSError:
        return None
    for name in entries:
        path = os.path.join(app_dir, name)
        if os.path.isfile(path) and _is_elf(path):
            try:
                if os.access(path, os.X_OK):
                    return path
            except OSError:
                pass
    return None


def scan_missing_libs(app_dir: str) -> list[str]:
    """Run ldd on the app's main executable and return missing shared libraries."""
    if not app_dir or not os.path.isdir(app_dir):
        return []
    target = _find_elf_target(app_dir)
    if not target:
        return []
    try:
        result = subprocess.run(
            ["ldd", target],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    missing: list[str] = []
    for line in result.stdout.splitlines():
        if "not found" in line:
            lib = line.split("=>")[0].strip()
            if lib:
                missing.append(lib)
    return sorted(set(missing))


def suggest_lib_fixes(missing_libs: list[str]) -> list[str]:
    """Map missing shared libraries to distro package install commands."""
    if not missing_libs:
        return []
    pm = detect_package_manager()
    if not pm:
        return []
    cmd = _INSTALL_CMD.get(pm)
    pkgs: set[str] = set()
    for lib in missing_libs:
        for soname, pkg_map in _COMMON_LIB_PACKAGES.items():
            if lib.startswith(soname):
                pkg = pkg_map.get(pm)
                if pkg:
                    pkgs.add(pkg)
                break
    if not pkgs:
        return []
    return [f"{cmd} {' '.join(sorted(pkgs))}"]


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

    missing_libs = scan_missing_libs(app_dir)
    if missing_libs:
        info["missing_libs"] = missing_libs
        issues.append(f"Missing shared libraries: {', '.join(missing_libs[:5])}")

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
    info: dict[str, Any] = {}
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
                if install_age > HEALTH_DAYS_THRESHOLD and "No updates checked" not in warnings:
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
    global _fuse_available
    if _fuse_available is not None:
        return _fuse_available
    try:
        result = subprocess.run(
            ["fusermount3", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            _fuse_available = True
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    try:
        result = subprocess.run(
            ["fusermount", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            _fuse_available = True
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    try:
        result = subprocess.run(
            ["which", "fusermount3", "fusermount"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        _fuse_available = bool(result.stdout.strip())
        return _fuse_available
    except (FileNotFoundError, subprocess.TimeoutExpired):
        _fuse_available = False
        return False


def check_namespace_available() -> bool:
    global _namespace_available
    if _namespace_available is not None:
        return _namespace_available
    try:
        result = subprocess.run(
            ["unshare", "--user", "--mount", "true"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        _namespace_available = result.returncode == 0
        return _namespace_available
    except (FileNotFoundError, subprocess.TimeoutExpired):
        _namespace_available = False
        return False


APPIMAGE_MAGIC = b"AI\x02"
APPIMAGE_MAGIC_ALT = b"AI\x01"


def check_appimage_magic(path: str) -> dict:
    """Validate AppImage magic bytes at offset 8 (type-2) or offset 0 (type-1)."""
    result = {"valid": False, "type": None, "error": ""}
    if not os.path.isfile(path):
        result["error"] = "File not found"
        return result
    try:
        with open(path, "rb") as f:
            header = f.read(12)
        if header[0:3] == APPIMAGE_MAGIC:
            result["valid"] = True
            result["type"] = "type2"
        elif header[0:3] == APPIMAGE_MAGIC_ALT:
            result["valid"] = True
            result["type"] = "type1"
        elif len(header) >= 8 and header[8:11] == APPIMAGE_MAGIC:
            result["valid"] = True
            result["type"] = "type2"
        else:
            result["error"] = "No AppImage magic header found"
    except OSError as e:
        result["error"] = str(e)
    return result


def check_system_compatibility() -> dict:
    issues: list[str] = []
    info: dict[str, Any] = {}
    u = os.uname()
    info["os"] = f"{u.sysname} {u.release}"
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    info["distro"] = line.split("=", 1)[1].strip().strip('"')
                    break
    except Exception as e:
        logger.debug("Failed to read /etc/os-release: %s", e, exc_info=True)
        info["distro"] = "unknown"
    info["kernel"] = u.version
    info["python"] = platform.python_version()
    try:
        r = subprocess.run(["rpm", "-q", "glibc", "--qf", "%{VERSION}"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            info["glibc"] = r.stdout.strip()
    except Exception as e:
        logger.debug("Failed to query glibc version: %s", e, exc_info=True)
    try:
        r = subprocess.run(
            ["rpm", "-q", "mesa-dri-drivers", "--qf", "%{VERSION}"], capture_output=True, text=True, timeout=5
        )
        if r.returncode == 0:
            info["mesa"] = r.stdout.strip()
    except Exception as e:
        logger.debug("Failed to query mesa version: %s", e, exc_info=True)
    return {"issues": issues, "info": info, "healthy": len(issues) == 0}


def format_health_icon(h: dict) -> str:
    if not h["healthy"]:
        return "emblem-important"
    if h["warnings"]:
        return "emblem-warning"
    return "emblem-default"
