"""Niruvi shared configuration — paths, data dir, settings persistence.

This module has ZERO UI imports. It is the single source of truth
for paths and settings used by all other modules.
"""

import logging
import os

from niruvi.utils.integrity import read_json_with_hmac, write_json_with_hmac

logger = logging.getLogger(__name__)

# ── Path Constants ──────────────────────────────────────────────────────

DEFAULT_INSTALL_DIR = os.path.expanduser("~/Applications")
DESKTOP_DIR = os.path.expanduser("~/.local/share/applications")
INSTALLED_DIR = os.path.expanduser("~/Applications/Niruvi")

# ── Settings State ──────────────────────────────────────────────────────

_settings: dict = {
    "install_dir": DEFAULT_INSTALL_DIR,
    "create_desktop": True,
    "create_shortcut": False,
    "portable_home": False,
    "portable_config": False,
    "icon_in_theme": True,
    "auto_scan_before_install": True,
    "register_mime_handler": True,
    "update_check_interval": "weekly",
    "auto_update_apps": False,
    "delta_updates": True,
    "tray_enabled": True,
    "close_to_tray": False,
    "sandbox_default_enabled": True,
    "sandbox_default_level": 2,
    "sandbox_default_backend": "shield",
    "auto_remove_source": False,
    "sound_effects_enabled": True,
    "sound_feedback_enabled": True,
    "sound_navigation_enabled": True,
    "sound_notifications_enabled": True,
    "sound_volume": 0.7,
    "sound_volume_feedback": 1.0,
    "sound_volume_navigation": 1.0,
    "sound_volume_notifications": 1.0,
    "privacy_allow_update_checks": True,
    "privacy_allow_background_updates": False,
    "virustotal_api_key": "",
    "store_index_url": "",
}


def get_data_dir() -> str:
    """Resolve the Niruvi data directory."""
    env = os.environ.get("NIRUVI_DATA_DIR")
    if env:
        return env
    if os.path.isfile(os.path.join(INSTALLED_DIR, "AppRun")):
        return os.path.join(INSTALLED_DIR, ".niruvi")
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        return os.path.join(os.path.dirname(appimage), ".niruvi")
    return os.path.expanduser("~/.config/niruvi")


def configure_runtime_env():
    """Set Qt/FFmpeg runtime env vars BEFORE QApplication is created.

    Silences benign Qt/KDE/FFmpeg log noise:
      - qt.qpa.wayland: "plugin supports grabbing the mouse only for popup windows"
      - qt.multimedia.ffmpeg: FFmpeg version banner + missing VDPAU backend fallback
      - kf.kio.widgets.kdirmodel: KDE native file dialog model desync warnings
    Also disables FFmpeg hardware decode probing (only sound effects are ever
    played, no video decoding is needed), which triggers the
    'Failed to open VDPAU backend libvdpau_nvidia.so' warning.
    """
    os.environ.setdefault(
        "QT_LOGGING_RULES",
        "qt.qpa.wayland.warning=false;"
        "qt.multimedia.ffmpeg.info=false;"
        "qt.multimedia.ffmpeg.warning=false;"
        "kf.kio.widgets.kdirmodel.warning=false",
    )
    os.environ.setdefault("QT_FFMPEG_DECODING_HW_DEVICE_TYPES", ",")
    # QSoundEffect with the default FFmpeg backend probes VDPAU hardware decode
    # on every creation, printing 'Failed to open VDPAU backend libvdpau_nvidia.so'
    # on systems without NVIDIA drivers. QT_FFMPEG_DECODING_HW_DEVICE_TYPES does
    # not suppress that probe; the GStreamer backend avoids it entirely and is
    # only used for audio here, so prefer it when the plugin is available.
    if _gstreamer_backend_available():
        os.environ.setdefault("QT_MEDIA_BACKEND", "gstreamer")


def _gstreamer_backend_available() -> bool:
    for plugin_dir in (
        "/usr/lib64/qt6/plugins/multimedia",
        "/usr/lib/x86_64-linux-gnu/qt6/plugins/multimedia",
        "/usr/lib/qt6/plugins/multimedia",
    ):
        if os.path.isfile(os.path.join(plugin_dir, "libgstreamermediaplugin.so")):
            return True
    return False


def get_settings() -> dict:
    return _settings


def _settings_file() -> str:
    return os.path.join(get_data_dir(), "settings.json")


def load_settings():
    sf = _settings_file()
    if os.path.exists(sf):
        loaded = read_json_with_hmac(sf)
        if loaded is not None:
            _settings.update(loaded)


def save_settings():
    data_dir = get_data_dir()
    os.makedirs(data_dir, exist_ok=True)
    sf = _settings_file()
    write_json_with_hmac(sf, _settings)
    logger.debug("Settings saved with HMAC integrity to %s", sf)
