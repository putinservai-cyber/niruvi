import json
import logging
import os
import shutil
import subprocess
import tempfile

from PyQt6.QtWidgets import QMessageBox

from niruvi.config import DESKTOP_DIR, get_settings, user_applications_dir
from niruvi.naming import safe_name, side_by_side_name
from niruvi.utils.icon_search import find_icon_in_dir as _find_icon_in_dir_impl

logger = logging.getLogger(__name__)


def sanitize_app_name(name: object) -> str:
    """Normalize an untrusted application name into a safe identifier.

    App names often come from an AppImage's embedded .desktop file and are
    used as directory names, .desktop filenames and inside generated
    desktop-entry/mimeapps content. Strip control characters (kills
    newline-based key injection), path separators and traversal sequences
    (kills ../ escapes), cap length.
    """
    return safe_name(name)


def find_unique_install_name(base_name: str, install_dir: str, registry=None) -> str:
    """Find a unique installation name for side-by-side installs.

    If base_name already exists in install_dir or registry, appends a numeric
    suffix (e.g., "App-2", "App-3") until a free name is found.

    Args:
        base_name: The desired base name (already sanitized)
        install_dir: The parent installation directory
        registry: Optional InstallationRegistry instance to check for existing records

    Returns:
        A unique name that doesn't conflict with existing installations.
    """
    base_name = sanitize_app_name(base_name)
    name = base_name
    suffix = 2
    while True:
        dest_path = os.path.join(install_dir, name)
        if not os.path.exists(dest_path):
            # Also check registry if provided
            if registry is not None:
                existing = registry.lookup_by_name(name)
                if existing is None:
                    return name
            else:
                return name
        name = side_by_side_name(base_name, suffix)
        suffix += 1
        if suffix > 1000:  # Safety limit
            raise RuntimeError("Too many side-by-side installations for this app")


def get_version(app_dir: str) -> str:
    meta_path = os.path.join(app_dir, ".appimage-manager.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path) as mf:
                meta = json.load(mf)
                version: str = meta.get("version", "unknown")
                return version
        except (json.JSONDecodeError, OSError):
            pass
    for f in os.listdir(app_dir):
        if f.endswith(".desktop"):
            desktop_path = os.path.join(app_dir, f)
            try:
                with open(desktop_path) as df:
                    for line in df:
                        stripped = line.strip()
                        # X-AppImage-Version is the standard field for app version
                        if stripped.startswith("X-AppImage-Version="):
                            return stripped.split("=", 1)[1]
                        # X-App-Version is used by some apps
                        if stripped.startswith("X-App-Version="):
                            return stripped.split("=", 1)[1]
            except OSError:
                pass
    return "unknown"


def find_icon_in_appdir(app_dir: str, icon_name: str) -> str | None:
    return _find_icon_in_dir_impl(app_dir, icon_name)


_ICON_SIZE_DIRS = {
    ".svg": "scalable",
    ".xpm": "48x48",
    ".ico": "48x48",
}

# Standard XDG icon sizes, ascending. A raster icon is filed under the size that
# best matches its real pixel dimensions so desktops never scale it up from a
# directory that claims a larger size than the artwork actually has.
_STANDARD_ICON_SIZES = (16, 22, 24, 32, 36, 48, 64, 72, 96, 128, 192, 256, 512)


def _png_dimensions(path: str) -> tuple[int, int] | None:
    """Read a PNG's pixel dimensions from its IHDR header (no Qt needed)."""
    try:
        with open(path, "rb") as f:
            header = f.read(24)
        if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        width = int.from_bytes(header[16:20], "big")
        height = int.from_bytes(header[20:24], "big")
        if width <= 0 or height <= 0:
            return None
        return width, height
    except OSError:
        return None


def _best_size_dir(width: int, height: int) -> str:
    longest = max(width, height)
    # Snap to the smallest standard bucket that can hold the artwork, so a
    # 200px icon lands in 256x256 rather than being declared 512x512.
    for size in _STANDARD_ICON_SIZES:
        if longest <= size:
            return f"{size}x{size}"
    return f"{_STANDARD_ICON_SIZES[-1]}x{_STANDARD_ICON_SIZES[-1]}"


def _icon_size_dir_for(icon_path: str, ext: str) -> str:
    if ext in _ICON_SIZE_DIRS:
        return _ICON_SIZE_DIRS[ext]
    if ext == ".png":
        dims = _png_dimensions(icon_path)
        if dims:
            return _best_size_dir(*dims)
        return "256x256"
    return "256x256"


def _sanitize_icon_name(name: str) -> str:
    """Turn an application name into a filesystem/XDG-safe icon identifier."""
    cleaned = "".join(c if (c.isalnum() or c in "-_.") else "-" for c in name.strip())
    cleaned = cleaned.strip("-.").lower()
    return cleaned or "application-x-executable"


def _format_exec_path(app_dir: str) -> str:
    """Format Exec= line for .desktop file, quoting the path per spec.

    Per the Desktop Entry Specification, Exec= values should be quoted
    if they contain spaces or special characters. We always quote for safety.
    """
    apprun = os.path.join(app_dir, "AppRun")
    return f'"{apprun}" %F'


def _format_tryexec_path(app_dir: str) -> str:
    """Format TryExec= line for .desktop file, quoting the path per spec."""
    apprun = os.path.join(app_dir, "AppRun")
    return f'"{apprun}"'


def _record_sandboxed(app_name: str) -> bool:
    """True when the app's registry record has sandboxing enabled."""
    try:
        from niruvi.desktop.installation_registry import InstallationRegistry

        record = InstallationRegistry().get(app_name)
        return bool(record and record.sandbox_config and record.sandbox_config.get("enabled", False))
    except Exception:
        return False


def _format_sandboxed_exec(app_name: str) -> str:
    """Exec= value that routes the launch through `niruvi --run` so the
    sandbox is applied even when launched from the desktop. Returns "" when
    no Niruvi launcher binary is available."""
    try:
        from niruvi.launcher import launcher_exec_line

        line = launcher_exec_line(app_name, fields="")
        if not line:
            return ""
        return line + " %F"
    except Exception:
        return ""


def install_icon_to_theme(icon_path: str, app_name: str) -> str | None:
    if not icon_path or not os.path.exists(icon_path):
        return None
    icon_path = str(icon_path)
    ext = os.path.splitext(icon_path)[1].lower()
    size_dir = _icon_size_dir_for(icon_path, ext)
    icon_name = _sanitize_icon_name(app_name)
    from niruvi.config import user_icons_dir

    icons_root = user_icons_dir()
    target_dir = os.path.join(icons_root, "hicolor", size_dir, "apps")
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, f"{icon_name}{ext}")
    try:
        shutil.copy2(icon_path, target)
        os.chmod(target, 0o644)
    except OSError as e:
        logging.warning("Failed to install icon to theme: %s", e)
        return None
    # Note: no PNG copy into scalable/ — that directory is for vector artwork
    # only. Filing a raster there makes icon caches treat it as an SVG and drop
    # it, which is one reason icons went missing after installation.
    return icon_name


def _resolve_icon(app_dir: str, desktop_lines: list[str] | None = None) -> str | None:
    if desktop_lines is None:
        desktop_files = [f for f in os.listdir(app_dir) if f.endswith(".desktop")]
        if not desktop_files:
            return None
        try:
            with open(os.path.join(app_dir, desktop_files[0])) as f:
                desktop_lines = f.readlines()
        except OSError:
            return None
    for line in desktop_lines or []:
        if line.startswith("Icon="):
            raw = line.split("=", 1)[1].strip()
            return find_icon_in_appdir(app_dir, raw)
    return None


def create_desktop_entry(app_dir: str, app_name: str, parent=None) -> str | None:
    # app_name may originate from an untrusted .desktop Name= field; never
    # let it influence filesystem paths or entry content.
    app_name = sanitize_app_name(app_name)
    desktop_files = [f for f in os.listdir(app_dir) if f.endswith(".desktop")]
    if not desktop_files:
        return _create_generic_desktop(app_dir, app_name)

    src_desktop = os.path.join(app_dir, desktop_files[0])
    dest_desktop = os.path.join(DESKTOP_DIR, f"{app_name}.desktop")

    try:
        with open(src_desktop) as f:
            lines = f.readlines()
    except OSError:
        return _create_generic_desktop(app_dir, app_name)

    icon_path = _resolve_icon(app_dir, lines)
    icon_name = install_icon_to_theme(icon_path, app_name) if icon_path else None

    new_lines = []
    has_exec = False
    has_categories = False
    has_startup = False
    sandboxed_exec = _format_sandboxed_exec(app_name) if _record_sandboxed(app_name) else ""
    for line in lines:
        if line.startswith("Exec="):
            new_lines.append(f"Exec={sandboxed_exec or _format_exec_path(app_dir)}\n")
            has_exec = True
        elif line.startswith("TryExec="):
            new_lines.append(f"TryExec={_format_tryexec_path(app_dir)}\n")
        elif line.startswith("Icon="):
            if icon_name:
                new_lines.append(f"Icon={icon_name}\n")
            else:
                new_lines.append(line)
        elif line.startswith("Categories="):
            has_categories = True
            new_lines.append(line)
        elif line.startswith("StartupNotify="):
            has_startup = True
            new_lines.append(line)
        else:
            new_lines.append(line)

    if not has_exec:
        new_lines.append(f"Exec={sandboxed_exec or _format_exec_path(app_dir)}\n")
    if not has_categories:
        new_lines.append("Categories=Utility;\n")
    if not has_startup:
        new_lines.append("StartupNotify=true\n")
    if not any(line.startswith("MimeType=") for line in new_lines):
        new_lines.append("MimeType=" + ";".join(_MIME_TYPES) + "\n")

    new_lines.append("X-Created-By=Niruvi\n")
    new_lines.append(f"X-AppImage-Path={app_dir}\n")

    try:
        with open(dest_desktop, "w") as f:
            f.writelines(new_lines)
        os.chmod(dest_desktop, 0o644)
        return dest_desktop
    except OSError as e:
        if parent:
            from niruvi.utils.sound_manager import play as play_sound

            play_sound("error")
            QMessageBox.critical(parent, "Error", f"Failed to write desktop file: {e}")
        return None


def create_desktop_shortcut(app_name: str, exec_path: str, icon_path: str | None = None) -> str | None:
    app_name = sanitize_app_name(app_name)
    from niruvi.config import user_desktop_dir

    desktop_dir = user_desktop_dir()
    os.makedirs(desktop_dir, exist_ok=True)
    shortcut_path = os.path.join(desktop_dir, f"{app_name}.desktop")

    icon_value = _sanitize_icon_name(app_name)
    if icon_path:
        installed = install_icon_to_theme(icon_path, app_name)
        if installed:
            icon_value = installed
        else:
            icon_value = icon_path

    sandboxed_exec = _format_sandboxed_exec(app_name) if _record_sandboxed(app_name) else ""
    # Always quote the exec path per Desktop Entry spec
    exec_value = sandboxed_exec or f'"{exec_path}"'
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={app_name}\n"
        "Comment=AppImage application managed by Niruvi\n"
        f"Exec={exec_value} %F\n"
        f"Icon={icon_value}\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
        "StartupNotify=true\n"
        "X-Created-By=Niruvi\n"
        f"X-AppImage-Path={exec_path}\n"
    )
    try:
        with open(shortcut_path, "w") as f:
            f.write(content)
        os.chmod(shortcut_path, 0o755)
        return shortcut_path
    except OSError:
        return None


def _create_generic_desktop(app_dir: str, app_name: str) -> str | None:
    app_name = sanitize_app_name(app_name)
    dest_desktop = os.path.join(DESKTOP_DIR, f"{app_name}.desktop")
    icon_path = _resolve_icon(app_dir)
    icon_name = install_icon_to_theme(icon_path, app_name) if icon_path else None
    content = (
        "[Desktop Entry]\n"
        f"Name={app_name}\n"
        f"Exec={_format_exec_path(app_dir)}\n"
        "Type=Application\n"
        f"Icon={icon_name or 'application-x-executable'}\n"
        "Categories=Utility;\n"
        "Comment=Extracted AppImage managed by Niruvi\n"
        "Terminal=false\n"
        "StartupNotify=true\n"
        "MimeType=" + ";".join(_MIME_TYPES) + "\n"
        "X-Created-By=Niruvi\n"
        f"X-AppImage-Path={app_dir}\n"
    )
    try:
        with open(dest_desktop, "w") as f:
            f.write(content)
        os.chmod(dest_desktop, 0o644)
        return dest_desktop
    except OSError:
        return None


def find_desktop_for_app(app_name: str) -> str | None:
    desktop_file = os.path.join(DESKTOP_DIR, f"{app_name}.desktop")
    if os.path.exists(desktop_file):
        return desktop_file
    try:
        from niruvi.desktop.installation_registry import InstallationRegistry

        record = InstallationRegistry().get(app_name)
        if record and record.desktop_file and os.path.exists(record.desktop_file):
            return record.desktop_file
    except Exception:
        pass
    install_dir = get_settings()["install_dir"]
    for f in os.listdir(DESKTOP_DIR):
        if f.endswith(".desktop"):
            path = os.path.join(DESKTOP_DIR, f)
            try:
                with open(path) as pf:
                    content = pf.read()
                    if f"Exec={install_dir}/{app_name}/AppRun" in content:
                        return path
            except OSError:
                pass
    return None


def find_desktop_shortcut(app_name: str) -> str | None:
    from niruvi.config import user_desktop_dir

    desktop_dir = user_desktop_dir()
    shortcut = os.path.join(desktop_dir, f"{app_name}.desktop")
    if os.path.exists(shortcut):
        return shortcut
    return None


def refresh_desktop_database() -> bool:
    from niruvi.config import user_icons_dir

    icons_dir = os.path.join(user_icons_dir(), "hicolor")
    apps_dir = DESKTOP_DIR
    any_success = False

    for cmd in [
        ["gtk-update-icon-cache", "-f", "-t", icons_dir],
        ["xdg-desktop-menu", "forceupdate"],
        ["update-desktop-database", apps_dir],
    ]:
        try:
            subprocess.run(cmd, capture_output=True, timeout=10)
            any_success = True
        except Exception as e:
            logger.debug("Failed to run desktop DB command %s: %s", cmd[0], e, exc_info=True)

    for kde_cmd in [["kbuildsycoca6"], ["kbuildsycoca5"]]:
        try:
            subprocess.run(kde_cmd, capture_output=True, timeout=30)
            any_success = True
            break
        except Exception as e:
            logger.debug("Failed to run KDE command %s: %s", kde_cmd[0], e, exc_info=True)
            continue

    return any_success


def rewrite_desktop_entry(app_name: str, new_exec: str, new_icon: str | None = None):
    desktop_path = find_desktop_for_app(app_name)
    if not desktop_path or not os.path.exists(desktop_path):
        return
    try:
        with open(desktop_path) as f:
            lines = f.readlines()
        new_lines = []
        for line in lines:
            if line.startswith("Exec="):
                new_lines.append(f"Exec={new_exec} %F\n")
            elif line.startswith("Icon=") and new_icon:
                new_lines.append(f"Icon={new_icon}\n")
            else:
                new_lines.append(line)
        with open(desktop_path, "w") as f:
            f.writelines(new_lines)
    except OSError:
        pass


def parse_desktop_file_content(content: str) -> dict:
    info = {}
    in_desktop = False
    for line in content.split("\n"):
        line = line.strip()
        if line == "[Desktop Entry]":
            in_desktop = True
            continue
        if in_desktop and line.startswith("[") and line.endswith("]"):
            break
        if in_desktop and "=" in line:
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            if key in ("Name", "Comment", "Icon", "Exec", "Categories", "Type", "Version"):
                info[key] = val
    return info


def parse_desktop_file(file_path: str) -> dict:
    try:
        with open(file_path, encoding="utf-8", errors="ignore") as f:
            return parse_desktop_file_content(f.read())
    except OSError:
        return {}


_MIME_TYPES = ("application/x-appimage", "application/vnd.appimage", "application/x-iso9660-appimage")


def register_mime_handler(app_name: str, desktop_name: str | None = None) -> bool:
    """Register an application as the default handler for AppImage MIME types.

    Uses xdg-mime and also writes directly to mimeapps.list for robustness
    across desktop environments. Existing entries are deduplicated instead of
    blindly appended so repeated installs do not grow the file.
    """
    desk = sanitize_app_name(desktop_name or app_name)
    desktop_file = f"{desk}.desktop"

    any_success = False
    for mt in _MIME_TYPES:
        try:
            subprocess.run(
                ["xdg-mime", "default", desktop_file, mt],
                capture_output=True,
                timeout=10,
            )
            any_success = True
        except Exception as e:
            logger.debug("Failed to set MIME handler for %s: %s", mt, e, exc_info=True)

    mimeapps = os.path.expanduser("~/.config/mimeapps.list")
    try:
        os.makedirs(os.path.dirname(mimeapps), exist_ok=True)
        existing = ""
        if os.path.isfile(mimeapps):
            with open(mimeapps) as f:
                existing = f.read()

        # Rebuild the file: strip stale handler lines for our MIME types from
        # [Default Applications] (keeps unrelated entries), then append ours
        # so repeated installs stay idempotent.
        default_entries: list[str] = []
        other_lines: list[str] = []
        current_section = ""
        for line in existing.splitlines():
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                current_section = stripped
                if stripped != "[Default Applications]":
                    other_lines.append(line)
                continue
            if current_section == "[Default Applications]":
                if any(line.startswith(f"{mt}=") for mt in _MIME_TYPES):
                    continue
                if stripped:
                    default_entries.append(line)
            else:
                other_lines.append(line)

        new_lines = ["[Default Applications]"]
        new_lines.extend(default_entries)
        for mt in _MIME_TYPES:
            new_lines.append(f"{mt}={desktop_file}")
        if other_lines:
            new_lines.append("")
            new_lines.extend(other_lines)

        # Atomic replace so a crash/interrupt can never leave a truncated
        # mimeapps.list behind (which would corrupt the user's MIME database).
        tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(mimeapps), prefix=".mimeapps.", suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w") as f:
                f.write("\n".join(new_lines) + "\n")
                f.flush()
                os.fsync(f.fileno())
            # Preserve the original file's mode (or a safe default)
            try:
                mode = os.stat(mimeapps).st_mode & 0o777
            except OSError:
                mode = 0o600
            os.chmod(tmp_path, mode)
            os.replace(tmp_path, mimeapps)
        except OSError:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        any_success = True
    except OSError as e:
        logger.debug("Failed to write %s: %s", mimeapps, e, exc_info=True)

    return any_success


def unregister_mime_handler(app_name: str, desktop_name: str | None = None) -> bool:
    """Remove this app's entries from mimeapps.list.

    Removes both ``[Default Applications]`` mappings to our desktop file and
    any ``[Added Associations]``/``[Removed Associations]`` references. The
    file is rewritten atomically like :func:`register_mime_handler`. Returns
    True if anything was actually removed.
    """
    desk = sanitize_app_name(desktop_name or app_name)
    desktop_file = f"{desk}.desktop"
    mimeapps = os.path.expanduser("~/.config/mimeapps.list")
    if not os.path.isfile(mimeapps):
        return False

    try:
        with open(mimeapps) as f:
            existing = f.read()
    except OSError as e:
        logger.debug("Failed to read %s: %s", mimeapps, e, exc_info=True)
        return False

    removed = False
    new_lines: list[str] = []
    current_section = ""
    target_sections = {"[Default Applications]", "[Added Associations]", "[Removed Associations]"}
    for line in existing.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current_section = stripped
            new_lines.append(line)
            continue
        if stripped and current_section in target_sections:
            key, sep, value = stripped.partition("=")
            if sep:
                entries = [e for e in value.split(";") if e]
                if desktop_file in entries:
                    removed = True
                    kept = [e for e in entries if e != desktop_file]
                    if kept:
                        new_lines.append(f"{key}=" + ";".join(kept) + ";")
                    continue
        new_lines.append(line)

    if not removed:
        return False

    try:
        tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(mimeapps), prefix=".mimeapps.", suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w") as f:
                f.write("\n".join(new_lines) + "\n")
                f.flush()
                os.fsync(f.fileno())
            try:
                mode = os.stat(mimeapps).st_mode & 0o777
            except OSError:
                mode = 0o600
            os.chmod(tmp_path, mode)
            os.replace(tmp_path, mimeapps)
        except OSError:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError as e:
        logger.warning("Failed to write %s: %s", mimeapps, e, exc_info=True)
        return False

    # Best-effort: drop the desktop file from the MIME cache where xdg tools exist.
    try:
        subprocess.run(
            ["update-desktop-database", user_applications_dir()],
            capture_output=True,
            timeout=10,
        )
    except Exception as e:
        logger.debug("update-desktop-database failed: %s", e, exc_info=True)
    return True


def create_portable_desktop_entry(appimage_path: str, app_name: str) -> str | None:
    """Create a .desktop entry for a *portable* install.

    In portable mode the AppImage itself is kept (moved/copied) rather than
    extracted, so the launcher points directly at the AppImage file.

    ``appimage_path`` is an absolute, already-sanitized path to the installed
    AppImage. ``app_name`` must already be sanitized by the caller.
    """
    app_name = sanitize_app_name(app_name)
    desktop_file = os.path.join(DESKTOP_DIR, f"{app_name}.desktop")
    # Always quote the exec path per Desktop Entry spec
    exec_value = f'"{appimage_path}"'
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={app_name}\n"
        "Comment=AppImage managed by Niruvi (portable mode)\n"
        f"Exec={exec_value} %F\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
        "StartupNotify=true\n"
        "MimeType=" + ";".join(_MIME_TYPES) + "\n"
        "X-Created-By=Niruvi\n"
        f"X-AppImage-Path={appimage_path}\n"
    )
    try:
        with open(desktop_file, "w") as f:
            f.write(content)
        os.chmod(desktop_file, 0o644)
        return desktop_file
    except OSError as e:
        logger.debug("Failed to write portable desktop entry: %s", e, exc_info=True)
        return None


def register_scheme_handler(
    scheme: str = "niruvi",
    handler_desktop: str = "niruvi-url-handler.desktop",
    exec_line: str = "niruvi %u",
) -> bool:
    """Register Niruvi as the handler for ``niruvi://`` URLs (one-click web
    installs via ``niruvi://install?url=…&sha256=…``).

    Writes a hidden .desktop file advertising ``x-scheme-handler/<scheme>`` and
    records it in mimeapps.list so it is idempotent across runs.
    """
    scheme = scheme.strip().lower()
    if not scheme or "/" in scheme or ";" in scheme:
        logger.warning("Refusing to register invalid scheme: %r", scheme)
        return False
    if not exec_line or ";" in exec_line or "\n" in exec_line:
        logger.warning("Refusing to register with unsafe Exec line")
        return False

    desktop_file = os.path.join(DESKTOP_DIR, handler_desktop)
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Niruvi URL Handler\n"
        "Comment=Handle niruvi:// URLs\n"
        f"Exec={exec_line}\n"
        f"MimeType=x-scheme-handler/{scheme};\n"
        "NoDisplay=true\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
    )
    try:
        os.makedirs(DESKTOP_DIR, exist_ok=True)
        with open(desktop_file, "w") as f:
            f.write(content)
        os.chmod(desktop_file, 0o644)
    except OSError as e:
        logger.debug("Failed to write scheme handler desktop: %s", e, exc_info=True)
        return False

    mime_type = f"x-scheme-handler/{scheme}"
    mimeapps = os.path.expanduser("~/.config/mimeapps.list")
    try:
        os.makedirs(os.path.dirname(mimeapps), exist_ok=True)
        existing = ""
        if os.path.isfile(mimeapps):
            with open(mimeapps) as f:
                existing = f.read()

        default_entries: list[str] = []
        added_section = False
        other_lines: list[str] = []
        current_section = ""
        for line in existing.splitlines():
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                current_section = stripped
                if stripped != "[Default Applications]":
                    other_lines.append(line)
                continue
            if current_section == "[Default Applications]":
                if line.startswith(f"{mime_type}="):
                    continue
                if stripped:
                    default_entries.append(line)
            else:
                other_lines.append(line)
        # Preserve an existing [Default Applications] section if present.
        for ln in other_lines:
            if ln.strip() == "[Default Applications]":
                added_section = True
                break

        new_lines = ["[Default Applications]"] if not added_section else []
        new_lines.extend(default_entries)
        new_lines.append(f"{mime_type}={handler_desktop}")
        if other_lines:
            if not added_section:
                new_lines.append("")
            new_lines.extend(other_lines)

        tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(mimeapps), prefix=".mimeapps.", suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w") as f:
                f.write("\n".join(new_lines) + "\n")
                f.flush()
                os.fsync(f.fileno())
            try:
                mode = os.stat(mimeapps).st_mode & 0o777
            except OSError:
                mode = 0o600
            os.chmod(tmp_path, mode)
            os.replace(tmp_path, mimeapps)
        except OSError:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError as e:
        logger.debug("Failed to register scheme handler in mimeapps: %s", e, exc_info=True)
        return False
    return True
