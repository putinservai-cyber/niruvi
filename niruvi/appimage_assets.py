import os
import subprocess
import tempfile
import shutil
from pathlib import Path

from niruvi.scanner import extract_safely


def _ensure_executable(path):
    st = os.stat(path)
    if not st.st_mode & 0o100:
        os.chmod(path, st.st_mode | 0o100)


def _find_squashfs_root(extract_dir):
    for p in Path(extract_dir).iterdir():
        if p.is_dir() and p.name.startswith("squashfs-root"):
            return p
    return None


def _extract_once(appimage_path):
    tmp = tempfile.TemporaryDirectory(prefix="aim-extract-")
    # Try safe extraction first (no code execution)
    safe_dir = os.path.join(tmp.name, "squashfs-root")
    if extract_safely(appimage_path, safe_dir):
        squashfs = _find_squashfs_root(tmp.name)
        if squashfs is not None:
            return tmp, squashfs

    # Fallback to --appimage-extract (executes the binary)
    _ensure_executable(appimage_path)
    try:
        subprocess.run(
            [appimage_path, "--appimage-extract"],
            cwd=tmp.name,
            check=True,
            capture_output=True,
            timeout=120,
        )
        squashfs = _find_squashfs_root(tmp.name)
        if squashfs is None:
            raise RuntimeError("Extraction produced no squashfs-root directory")
        return tmp, squashfs
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        try:
            tmp.cleanup()
        except OSError:
            pass
        raise RuntimeError(f"AppImage extraction failed: {e}") from e


def _extract_one(squashfs, extract_dir, kind):
    extract_dir = Path(extract_dir)
    if kind == "desktop":
        files = list(squashfs.glob("*.desktop"))
        if not files:
            return None
        dest = extract_dir / files[0].name
        if dest.exists():
            dest.unlink()
        shutil.copy2(str(files[0]), str(dest))
        return str(dest)
    elif kind == "icon":
        icon_name = _get_icon_name_from_desktop(squashfs)
        f = _find_icon_in_dir(squashfs, icon_name)
        if f is None:
            return None
        dest = extract_dir / f.name
        if dest.exists():
            dest.unlink()
        shutil.copy2(str(f), str(dest))
        return str(dest)
    elif kind == "apprun":
        apprun = squashfs / "AppRun"
        if not apprun.exists():
            return None
        dest = extract_dir / "AppRun"
        if dest.exists():
            dest.unlink()
        shutil.copy2(str(apprun), str(dest))
        os.chmod(str(dest), 0o755)
        return str(dest)
    return None


def extract_desktop_entry(appimage_path, extract_dir):
    tmp, squashfs = _extract_once(appimage_path)
    try:
        result = _extract_one(squashfs, extract_dir, "desktop")
        if result is None:
            raise RuntimeError("No .desktop file found in AppImage")
        return Path(result)
    finally:
        tmp.cleanup()


def extract_icon(appimage_path, extract_dir):
    tmp, squashfs = _extract_once(appimage_path)
    try:
        result = _extract_one(squashfs, extract_dir, "icon")
        if result is None:
            raise RuntimeError("No icon found in AppImage")
        return Path(result)
    finally:
        tmp.cleanup()


def extract_apprun(appimage_path, extract_dir):
    tmp, squashfs = _extract_once(appimage_path)
    try:
        result = _extract_one(squashfs, extract_dir, "apprun")
        if result is None:
            raise RuntimeError("AppRun not found in extracted AppImage")
        return Path(result)
    finally:
        tmp.cleanup()


def extract_metadata(appimage_path, extract_dir):
    """Extract desktop file, icon, and AppRun in a single pass.

    Returns a dict with keys "desktop", "icon", "apprun"
    for each asset that was successfully extracted.
    """
    extract_dir = Path(extract_dir)
    tmp, squashfs = _extract_once(appimage_path)
    try:
        result = {}
        for kind in ("desktop", "icon", "apprun"):
            p = _extract_one(squashfs, extract_dir, kind)
            if p is not None:
                result[kind] = p
        return result
    finally:
        tmp.cleanup()


def _get_icon_name_from_desktop(extracted_dir):
    for df in extracted_dir.glob("*.desktop"):
        try:
            content = df.read_text(encoding='utf-8', errors='ignore')
            in_desktop = False
            for line in content.split('\n'):
                line = line.strip()
                if line == '[Desktop Entry]':
                    in_desktop = True
                    continue
                if in_desktop and line.startswith('[') and line.endswith(']'):
                    break
                if in_desktop and line.startswith('Icon='):
                    icon = line.split('=', 1)[1].strip()
                    if icon and icon != "application-x-executable":
                        return icon
        except OSError:
            continue
    return None


def _find_icon_in_dir(extracted_dir, icon_name):
    if icon_name:
        for size_dir in ("scalable", "256x256", "128x128", "64x48", "48x48"):
            for ext in (".png", ".svg", ".xpm", ""):
                candidate = extracted_dir / "usr" / "share" / "icons" / "hicolor" / size_dir / "apps" / f"{icon_name}{ext}"
                if candidate.exists() and candidate.stat().st_size > 0:
                    return candidate
        for candidate in extracted_dir.rglob(f"{icon_name}*"):
            if candidate.is_file() and candidate.suffix in (".png", ".svg", ".xpm", "") and candidate.stat().st_size > 0:
                return candidate

    dir_icon = extracted_dir / ".DirIcon"
    if dir_icon.exists():
        target = dir_icon.resolve() if dir_icon.is_symlink() else dir_icon
        if target.is_file() and target.stat().st_size > 0:
            return target

    for icon_dir in [
        extracted_dir / "usr" / "share" / "icons" / "hicolor" / "scalable" / "apps",
        extracted_dir / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps",
        extracted_dir / "usr" / "share" / "icons" / "hicolor" / "128x128" / "apps",
    ]:
        if icon_dir.exists():
            for icon_file in icon_dir.iterdir():
                if icon_file.is_file() and icon_file.suffix in (".png", ".svg", ".xpm") and icon_file.stat().st_size > 0:
                    return icon_file

    icons_base = extracted_dir / "usr" / "share" / "icons"
    if icons_base.exists():
        best = None
        best_size = 0
        for ext in ("*.png", "*.svg", "*.xpm"):
            for icon_file in icons_base.rglob(ext):
                if icon_file.is_file() and icon_file.stat().st_size > best_size:
                    best = icon_file
                    best_size = icon_file.stat().st_size
        if best:
            return best

    best = None
    best_size = 0
    for ext in ("*.png", "*.svg", "*.xpm"):
        for icon_file in extracted_dir.rglob(ext):
            if icon_file.is_file() and icon_file.stat().st_size > best_size:
                best = icon_file
                best_size = icon_file.stat().st_size
    return best
