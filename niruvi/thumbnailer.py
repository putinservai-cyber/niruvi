"""Freedesktop-compatible AppImage thumbnailer.

Generates PNG thumbnails for .AppImage files so file managers
(Nautilus, Nemo, Thunar, Dolphin) show app icons.

Registration:
  - Installs a .thumbnailer file to ~/.local/share/thumbnailers/
  - Installs a helper script to ~/.local/bin/niruvi-thumbnailer
"""

import logging
import os
import shutil
import stat
import struct
import subprocess
import sys

logger = logging.getLogger(__name__)

THUMBNAILER_DIR = os.path.expanduser("~/.local/share/thumbnailers")
THUMBNAILER_FILE = os.path.join(THUMBNAILER_DIR, "niruvi-appimage.thumbnailer")
HELPER_DIR = os.path.expanduser("~/.local/bin")
HELPER_PATH = os.path.join(HELPER_DIR, "niruvi-thumbnailer")
MIME_TYPES = "application/x-appimage;application/vnd.appimage;application/x-iso9660-appimage;"


def check_thumbnailer_installed() -> bool:
    """Check if our thumbnailer is registered."""
    return os.path.isfile(THUMBNAILER_FILE) and os.path.isfile(HELPER_PATH)


def install_thumbnailer() -> str | None:
    """Install the thumbnailer. Returns error message or None on success."""
    try:
        os.makedirs(THUMBNAILER_DIR, exist_ok=True)
        os.makedirs(HELPER_DIR, exist_ok=True)
        _write_thumbnailer_file()
        _write_helper_script()
        logger.info("Thumbnailer installed")
        return None
    except OSError as e:
        return f"Cannot install thumbnailer: {e}"


def remove_thumbnailer() -> str | None:
    """Remove the thumbnailer. Returns error message or None on success."""
    try:
        for path in (THUMBNAILER_FILE, HELPER_PATH):
            if os.path.exists(path):
                os.remove(path)
        logger.info("Thumbnailer removed")
        return None
    except OSError as e:
        return f"Cannot remove thumbnailer: {e}"


def _write_thumbnailer_file():
    content = f"""[Thumbnailer Entry]
TryExec={HELPER_PATH}
Exec={HELPER_PATH} %i %o %s
MimeType={MIME_TYPES}
"""
    tmp = THUMBNAILER_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write(content)
    os.replace(tmp, THUMBNAILER_FILE)


def _write_helper_script():
    content = "#!/bin/sh\nexec python3 -m niruvi.thumbnailer \"$@\"\n"
    tmp = HELPER_PATH + ".tmp"
    with open(tmp, "w") as f:
        f.write(content)
    os.chmod(tmp, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    os.replace(tmp, HELPER_PATH)


def _is_appimage(path: str) -> bool:
    """Check if file has valid ELF magic + AppImage offset."""
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "rb") as f:
            header = f.read(8)
        return header[:4] == b"\x7fELF" and header[4:8] == b"\x41\x49\x02\x01"
    except OSError:
        return False


def generate_thumbnail(input_path: str, output_path: str, size: int = 256):
    """Generate a PNG thumbnail for an AppImage.

    Args:
        input_path: Path to the .AppImage file
        output_path: Where to write the PNG thumbnail
        size: Preferred size in pixels
    """
    if not _is_appimage(input_path):
        logger.warning("Not a valid AppImage: %s", input_path)
        return

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    icon_data = _extract_icon(input_path)
    if not icon_data:
        logger.debug("No icon found in %s, using fallback", input_path)
        icon_data = _fallback_icon(input_path)

    if icon_data:
        try:
            from niruvi.icon_utils import to_png_bytes, save_icon_to_png
            png = to_png_bytes(icon_data)
            if png:
                save_icon_to_png(png, output_path)
                return
        except Exception as e:
            logger.debug("Icon conversion failed: %s", e)

    # Last resort: write an empty file so tumbler doesn't retry
    try:
        open(output_path, "a").close()
    except OSError:
        pass


def _extract_icon(appimage_path: str) -> bytes | None:
    """Extract the app icon from an AppImage's embedded filesystem."""
    if not os.access(appimage_path, os.X_OK):
        os.chmod(appimage_path, 0o755)

    import tempfile
    extract_dir = tempfile.mkdtemp(prefix="niruvi-thumb-")
    try:
        r = subprocess.run(
            [appimage_path, "--appimage-extract"],
            cwd=extract_dir,
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            logger.debug("Extraction returned %d: %s", r.returncode, r.stderr[:200])
            return None

        root = os.path.join(extract_dir, "squashfs-root")
        if not os.path.isdir(root):
            alt = os.path.join(extract_dir, "AppDir")
            if os.path.isdir(alt):
                root = alt
            else:
                return None

        icon_bytes = _find_icon_in_dir(root)
        if icon_bytes:
            return icon_bytes

        for f in os.listdir(root):
            if f.endswith(".desktop"):
                try:
                    with open(os.path.join(root, f), errors="replace") as df:
                        for line in df:
                            if line.startswith("Icon="):
                                icon_name = line.split("=", 1)[1].strip()
                                icon_bytes = _find_named_icon(root, icon_name)
                                if icon_bytes:
                                    return icon_bytes
                except OSError:
                    pass
        return None
    except subprocess.TimeoutExpired:
        logger.debug("Extraction timed out for %s", appimage_path)
        return None
    except Exception as e:
        logger.debug("Extraction failed: %s", e)
        return None
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)


def _find_icon_in_dir(root: str) -> bytes | None:
    """Find the best icon file in an extracted AppDir and return its bytes."""
    candidates = []
    for dirpath, _, filenames in os.walk(root):
        for f in filenames:
            if f.endswith((".png", ".svg", ".xpm")):
                path = os.path.join(dirpath, f)
                try:
                    size = os.path.getsize(path)
                    candidates.append((size, path))
                except OSError:
                    pass

    if not candidates:
        return None

    # Prefer .png over other formats, larger is better
    pngs = [(s, p) for s, p in candidates if p.endswith(".png")]
    if pngs:
        pngs.sort(key=lambda x: -x[0])
        _, best = pngs[0]
    else:
        candidates.sort(key=lambda x: -x[0])
        _, best = candidates[0]

    try:
        with open(best, "rb") as f:
            return f.read()
    except OSError:
        return None


def _find_named_icon(root: str, icon_name: str) -> bytes | None:
    """Find icon by name in standard Freedesktop paths."""
    search_dirs = [
        os.path.join(root, "usr", "share", "icons", "hicolor"),
        os.path.join(root, "usr", "share", "pixmaps"),
        root,
    ]
    for search_dir in search_dirs:
        if not os.path.isdir(search_dir):
            continue
        for dirpath, _, filenames in os.walk(search_dir):
            for f in filenames:
                name, ext = os.path.splitext(f)
                if name == icon_name and ext in (".png", ".svg", ".xpm"):
                    try:
                        with open(os.path.join(dirpath, f), "rb") as fh:
                            return fh.read()
                    except OSError:
                        pass
    return None


def _fallback_icon(appimage_path: str) -> bytes | None:
    """Search for embedded PNG data in the AppImage binary."""
    try:
        with open(appimage_path, "rb") as f:
            data = f.read(65536)
        idx = data.find(b"\x89PNG")
        if idx >= 0:
            # Walk PNG chunks starting from the found PNG, find IEND
            pos = idx + 8
            end = 0
            while pos + 8 <= len(data):
                length = struct.unpack_from(">I", data, pos)[0]
                chunk_type = data[pos + 4:pos + 8]
                if chunk_type == b"IEND":
                    end = pos + 8 + length + 4
                    break
                pos += 12 + length
            if end > 0 and end <= len(data):
                return data[idx:end]
            return data[idx:]
    except OSError:
        pass
    return None


if __name__ == "__main__":
    if len(sys.argv) >= 4:
        generate_thumbnail(sys.argv[1], sys.argv[2], int(sys.argv[3]))
