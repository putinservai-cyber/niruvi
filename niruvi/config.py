"""Niruvi shared configuration — paths, data dir, settings persistence.

This module has ZERO UI imports. It is the single source of truth
for paths and settings used by all other modules.
"""

import json
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
    "update_check_interval": "weekly",
    "auto_update_apps": False,
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
        else:
            try:
                with open(sf) as f:
                    loaded = json.load(f)
                    if "_data" in loaded:
                        loaded = loaded.get("_data", {})
                    _settings.update(loaded)
                    logger.info("Loaded settings without HMAC (legacy format)")
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Corrupted settings file: %s", e)


def save_settings():
    data_dir = get_data_dir()
    os.makedirs(data_dir, exist_ok=True)
    sf = _settings_file()
    write_json_with_hmac(sf, _settings)
    logger.debug("Settings saved with HMAC integrity to %s", sf)
