"""JuNest container AppImage detection and path-with-spaces guidance.

Some AppImages (e.g. the VLC media player AppImage) bundle a JuNest
environment — a minimal Arch Linux filesystem inside ``.junest/`` — that
relies on bubblewrap/proot scripts which word-split unquoted shell
variables.  Installing such an AppImage into a path containing spaces
(e.g. ``~/Applications/VLC media player``) breaks these scripts (see
``vlc-appimage-fixes.md``).  This module detects such AppImages and
helps steer installs to space-free paths.
"""

import logging
import os
import re

logger = logging.getLogger(__name__)

_JUNEST_MARKERS = (
    ".junest",
    os.path.join(".local", "share", "junest"),
    os.path.join("usr", "share", "junest"),
)


class JunestPathError(RuntimeError):
    """Raised when a JuNest AppImage is about to be installed to a path with spaces."""

    def __init__(self, message: str, suggested_path: str = ""):
        super().__init__(message)
        self.suggested_path = suggested_path


def is_junest_app(extracted_dir: str) -> bool:
    """Return True if an extracted AppImage root bundles a JuNest environment."""
    if not extracted_dir or not os.path.isdir(extracted_dir):
        return False
    for marker in _JUNEST_MARKERS:
        if os.path.isdir(os.path.join(extracted_dir, marker)):
            return True
    junest_bin = os.path.join(extracted_dir, "lib", "core", "namespace.sh")
    if os.path.isfile(junest_bin):
        try:
            with open(junest_bin, errors="ignore") as fh:
                return "COMMON_BWRAP_OPTION" in fh.read(4096)
        except OSError:
            logger.debug("Could not read %s", junest_bin, exc_info=True)
    return False


def path_has_spaces(path: str) -> bool:
    """Return True if any component of the path contains whitespace."""
    return bool(path) and any(c.isspace() for c in os.path.basename(path.rstrip(os.sep)))


def suggest_space_free_path(path: str) -> str:
    """Return ``path`` with whitespace in the last component replaced by dashes."""
    base = os.path.dirname(path.rstrip(os.sep))
    name = os.path.basename(path.rstrip(os.sep))
    if not path_has_spaces(path):
        return path
    cleaned = re.sub(r"\s+", "-", name)
    if not cleaned:
        return path
    return os.path.join(base, cleaned)


def _junest_path_message(dest_dir: str, suggested: str) -> str:
    return (
        f"This app bundles a JuNest container, which can fail when installed to a path "
        f"containing spaces:\n\n{dest_dir}\n\n"
        f"If the app fails to launch (e.g. 'bwrap: execvp: No such file or directory' "
        f"or 'command not found'), reinstall it to a path without spaces, for example:\n"
        f"{suggested}"
    )


def junest_destination_error(extracted_dir: str, dest_dir: str) -> str | None:
    """Return an error message if a JuNest AppImage would break in ``dest_dir``."""
    if not path_has_spaces(dest_dir) or not is_junest_app(extracted_dir):
        return None
    return _junest_path_message(dest_dir, suggest_space_free_path(dest_dir))


def junest_install_warning(extracted_dir: str, dest_dir: str) -> str | None:
    """Return a warning message if a JuNest AppImage is installed to a path with spaces."""
    if not path_has_spaces(dest_dir) or not is_junest_app(extracted_dir):
        return None
    return _junest_path_message(dest_dir, suggest_space_free_path(dest_dir))
