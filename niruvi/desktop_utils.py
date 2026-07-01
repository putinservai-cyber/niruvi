import json
import logging
import os
import shutil
import subprocess

from PyQt6.QtWidgets import QMessageBox
from pathlib import Path

from niruvi.settings import DESKTOP_DIR, get_settings
from niruvi.sound_manager import play as play_sound


def get_version(app_dir: str) -> str:
    meta_path = os.path.join(app_dir, ".appimage-manager.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path) as f:
                meta = json.load(f)
                return meta.get("version", "unknown")
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
    if not icon_name:
        return None
    if os.path.isabs(icon_name) and os.path.exists(icon_name):
        return icon_name
    extensions = [".png", ".svg", ".xpm", ".ico"]
    search_dirs = [
        app_dir,
        os.path.join(app_dir, "usr", "share", "icons"),
        os.path.join(app_dir, "usr", "share", "pixmaps"),
        os.path.join(app_dir, "usr", "local", "share", "icons"),
    ]
    for base in search_dirs:
        if not os.path.isdir(base):
            continue
        for ext in extensions:
            candidate = os.path.join(base, icon_name + ext)
            if os.path.exists(candidate):
                return candidate
        candidate = os.path.join(base, icon_name)
        if os.path.exists(candidate):
            return candidate
        for root, _, files in os.walk(base):
            for f in files:
                if f == icon_name or any(f == icon_name + ext for ext in extensions):
                    return os.path.join(root, f)
    return None


_ICON_SIZE_DIRS = {
    ".png": "256x256",
    ".svg": "scalable",
    ".xpm": "48x48",
    ".ico": "48x48",
}


def _sanitize_icon_name(name: str) -> str:
    return name.replace(" ", "-").replace("\t", "-").lower()


def _format_exec_path(app_dir: str) -> str:
    apprun = os.path.join(app_dir, "AppRun")
    if " " in apprun:
        return f'"{apprun}" %F'
    return f"{apprun} %F"


def install_icon_to_theme(icon_path: str, app_name: str) -> str | None:
    if not icon_path or not os.path.exists(icon_path):
        return None
    icon_path = str(icon_path)
    ext = os.path.splitext(icon_path)[1].lower()
    size_dir = _ICON_SIZE_DIRS.get(ext, "256x256")
    icon_name = _sanitize_icon_name(app_name)
    icons_root = os.path.expanduser("~/.local/share/icons")
    target_dir = os.path.join(icons_root, "hicolor", size_dir, "apps")
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, f"{icon_name}{ext}")
    try:
        shutil.copy2(icon_path, target)
        os.chmod(target, 0o644)
    except OSError as e:
        logging.warning("Failed to install icon to theme: %s", e)
        return None
    # For PNG, also install a copy to scalable for fallback
    if ext == ".png":
        scalable_dir = os.path.join(icons_root, "hicolor", "scalable", "apps")
        os.makedirs(scalable_dir, exist_ok=True)
        scalable_target = os.path.join(scalable_dir, f"{icon_name}{ext}")
        try:
            shutil.copy2(icon_path, scalable_target)
            os.chmod(scalable_target, 0o644)
        except OSError:
            pass
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
    for line in (desktop_lines or []):
        if line.startswith("Icon="):
            raw = line.split("=", 1)[1].strip()
            return find_icon_in_appdir(app_dir, raw)
    return None


def create_desktop_entry(app_dir: str, app_name: str, parent=None) -> str | None:
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
    for line in lines:
        if line.startswith("Exec="):
            new_lines.append(f"Exec={_format_exec_path(app_dir)}\n")
            has_exec = True
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
        new_lines.append(f"Exec={_format_exec_path(app_dir)}\n")
    if not has_categories:
        new_lines.append("Categories=Utility;\n")
    if not has_startup:
        new_lines.append("StartupNotify=true\n")

    new_lines.append("X-Created-By=Niruvi\n")
    new_lines.append(f"X-AppImage-Path={app_dir}\n")

    try:
        with open(dest_desktop, "w") as f:
            f.writelines(new_lines)
        os.chmod(dest_desktop, 0o644)
        return dest_desktop
    except OSError as e:
        if parent:
            play_sound("error")
            QMessageBox.critical(parent, "Error", f"Failed to write desktop file: {e}")
        return None


def create_desktop_shortcut(app_name: str, exec_path: str, icon_path: str | None = None) -> str | None:
    desktop_dir = os.path.expanduser("~/Desktop")
    os.makedirs(desktop_dir, exist_ok=True)
    shortcut_path = os.path.join(desktop_dir, f"{app_name}.desktop")

    icon_value = _sanitize_icon_name(app_name)
    if icon_path:
        installed = install_icon_to_theme(icon_path, app_name)
        if installed:
            icon_value = installed
        else:
            icon_value = icon_path

    exec_value = f'"{exec_path}"' if " " in exec_path else exec_path
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
    install_dir = get_settings()["install_dir"]
    desktop_file = os.path.join(DESKTOP_DIR, f"{app_name}.desktop")
    if os.path.exists(desktop_file):
        return desktop_file
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
    desktop_dir = os.path.expanduser("~/Desktop")
    shortcut = os.path.join(desktop_dir, f"{app_name}.desktop")
    if os.path.exists(shortcut):
        return shortcut
    return None


def refresh_desktop_database() -> bool:
    icons_dir = os.path.expanduser("~/.local/share/icons/hicolor")
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
        except Exception:
            pass

    for kde_cmd in [["kbuildsycoca6"], ["kbuildsycoca5"]]:
        try:
            subprocess.run(kde_cmd, capture_output=True, timeout=30)
            any_success = True
            break
        except Exception:
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
    for line in content.split('\n'):
        line = line.strip()
        if line == '[Desktop Entry]':
            in_desktop = True
            continue
        if in_desktop and line.startswith('[') and line.endswith(']'):
            break
        if in_desktop and '=' in line:
            key, val = line.split('=', 1)
            key = key.strip()
            val = val.strip()
            if key in ('Name', 'Comment', 'Icon', 'Exec', 'Categories', 'Type', 'Version'):
                info[key] = val
    return info


def parse_desktop_file(file_path: str) -> dict:
    try:
        with open(file_path, encoding='utf-8', errors='ignore') as f:
            return parse_desktop_file_content(f.read())
    except OSError:
        return {}



