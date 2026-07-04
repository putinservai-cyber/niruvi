"""Qt platform path fix for AppImage environments."""

import logging
import os

logger = logging.getLogger(__name__)


def fix_qt_platform_path():
    """Ensure Qt can find its platform plugins when running from an AppImage.

    The AppImage runtime may set QT_QPA_PLATFORM_PLUGIN_PATH to an empty
    or non-existent path, causing QApplication creation to fail. This
    cleans LD_LIBRARY_PATH and finds the correct Qt6 plugin directory.

    When running from an AppImage, also prefers xcb over wayland to avoid
    loading an incompatible bundled Wayland plugin.
    """
    appimage = os.environ.get("APPIMAGE")
    if appimage and not os.environ.get("QT_QPA_PLATFORM"):
        os.environ["QT_QPA_PLATFORM"] = "xcb"

    old = os.environ.get("LD_LIBRARY_PATH", "")
    if old:
        cleaned = [p for p in old.split(":") if p and not p.startswith("/tmp/.mount_")]
        if cleaned:
            os.environ["LD_LIBRARY_PATH"] = ":".join(cleaned)
        else:
            os.environ.pop("LD_LIBRARY_PATH", None)

    def _has_platform_plugins(path: str) -> bool:
        platforms = os.path.join(path, "platforms")
        if not os.path.isdir(platforms):
            return False
        return any(f.startswith("libq") and f.endswith(".so") for f in os.listdir(platforms))

    cur = os.environ.get("QT_QPA_PLATFORM_PLUGIN_PATH", "")
    if cur and _has_platform_plugins(cur):
        return

    candidates = [
        os.path.join(os.environ.get("APPDIR", ""), "usr", "lib64", "qt6", "plugins"),
        "/usr/lib64/qt6/plugins",
        "/usr/lib/x86_64-linux-gnu/qt6/plugins",
    ]
    for p in candidates:
        if _has_platform_plugins(p):
            os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = p
            return
    os.environ.pop("QT_QPA_PLATFORM_PLUGIN_PATH", None)
