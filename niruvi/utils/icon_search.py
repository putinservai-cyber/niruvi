"""Shared icon search utilities."""

import logging
import os

logger = logging.getLogger(__name__)

_ICON_EXTENSIONS = (".png", ".svg", ".xpm", ".ico")


def find_icon_in_dir(base_dir: str, icon_name: str) -> str | None:
    """Find an icon by name inside a directory tree.

    Searches standard Freedesktop paths, then falls back to walk.
    """
    if not icon_name:
        return None
    if os.path.isabs(icon_name) and os.path.exists(icon_name):
        return icon_name

    search_dirs = [
        base_dir,
        os.path.join(base_dir, "usr", "share", "icons"),
        os.path.join(base_dir, "usr", "share", "pixmaps"),
        os.path.join(base_dir, "usr", "local", "share", "icons"),
    ]
    for base in search_dirs:
        if not os.path.isdir(base):
            continue
        for ext in _ICON_EXTENSIONS:
            candidate = os.path.join(base, icon_name + ext)
            if os.path.exists(candidate):
                return candidate
        candidate = os.path.join(base, icon_name)
        if os.path.exists(candidate):
            return candidate
        for root, _, files in os.walk(base):
            for f in files:
                if f == icon_name or any(f == icon_name + ext for ext in _ICON_EXTENSIONS):
                    return os.path.join(root, f)
    return None


def find_best_icon_in_extracted(root: str) -> str | None:
    """Find the best icon file in an extracted AppDir.

    Prefers .png, then larger files.
    """
    candidates = []
    for dirpath, _, filenames in os.walk(root):
        for f in filenames:
            if f.endswith(_ICON_EXTENSIONS[:3]):
                path = os.path.join(dirpath, f)
                try:
                    size = os.path.getsize(path)
                    candidates.append((size, path))
                except OSError:
                    pass
    if not candidates:
        return None
    pngs = [(s, p) for s, p in candidates if p.endswith(".png")]
    if pngs:
        pngs.sort(key=lambda x: -x[0])
        return pngs[0][1]
    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1]
