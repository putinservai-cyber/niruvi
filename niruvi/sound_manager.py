"""Sound effects system for Niruvi UI.

Provides fire-and-forget MP3 playback via subprocess (paplay/ffplay/aplay)
without blocking the UI. Covers QAbstractButton clicks (QPushButton, QCheckBox,
QRadioButton), QMenu actions, ToggleSwitch toggles, and keyboard-activated buttons.
"""

import logging
import os
import shutil
import subprocess
import time

from PyQt6.QtCore import QObject, Qt, QEvent
from PyQt6.QtWidgets import QApplication, QMenu, QAbstractButton

from niruvi.settings import _settings
from niruvi.toggle_switch import ToggleSwitch

logger = logging.getLogger(__name__)

_SOUND_MAP: dict[str, str] = {
    "click": "computer-mouse-click-352734.mp3",
    "error": "error-012-132111.mp3",
    "warning": "system-error-notice-132470.mp3",
    "interface": "interface-2-126517.mp3",
    "navigation": "navigation-sound-1-269298.mp3",
    "info": "new-information-153314.mp3",
    "notification": "system-notification-02-352442.mp3",
    "toggle": "computer-mouse-click-352734.mp3",
}

_PLAYER_PRIORITY = ["paplay", "ffplay", "aplay"]

_audio_dir: str = ""
_player: str | None = None
_initialized = False
_sound_filter: QObject | None = None
_last_play_time: float = 0.0
_PLAY_DEBOUNCE_MS: float = 0.3


def _find_audio_dir() -> str:
    candidates = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "asset", "audio"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "asset", "audio"),
    ]
    appdir = os.environ.get("APPDIR")
    if appdir:
        candidates.insert(0, os.path.join(appdir, "asset", "audio"))
    for path in candidates:
        norm = os.path.normpath(os.path.abspath(path))
        if os.path.isdir(norm):
            return norm
    logger.warning("audio directory not found, checked: %s", candidates)
    return ""


def _init():
    global _audio_dir, _player, _initialized
    if _initialized:
        return
    _audio_dir = _find_audio_dir()
    for p in _PLAYER_PRIORITY:
        found = shutil.which(p)
        if found:
            _player = p
            break
    _initialized = True
    if not _player:
        logger.warning("No audio player found (tried: %s)", _PLAYER_PRIORITY)


def play(sound_name: str):
    global _last_play_time
    if not _settings.get("sound_effects_enabled", True):
        return
    now = time.time()
    if now - _last_play_time < _PLAY_DEBOUNCE_MS:
        return
    _last_play_time = now
    _init()
    if not _player or not _audio_dir:
        return
    filename = _SOUND_MAP.get(sound_name)
    if not filename:
        logger.warning("Unknown sound: %s", sound_name)
        return
    filepath = os.path.join(_audio_dir, filename)
    if not os.path.isfile(filepath):
        logger.warning("Sound file not found: %s", filepath)
        return

    try:
        if _player == "ffplay":
            subprocess.Popen(
                [_player, "-nodisp", "-autoexit", "-loglevel", "quiet", filepath],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            subprocess.Popen(
                [_player, filepath],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception as e:
        logger.warning("Sound playback failed: %s", e)


def install_menu_sound(menu: QMenu):
    """Connect a QMenu's triggered signal to play a click sound."""
    try:
        menu.triggered.connect(lambda: play("click"))
    except Exception:
        pass


class _GlobalSoundFilter(QObject):
    """Event filter for QPushButton/QCheckBox clicks (mouse + keyboard) and ToggleSwitch."""

    def eventFilter(self, obj, event):
        if isinstance(obj, QAbstractButton) and obj.isEnabled():
            if event.type() == QEvent.Type.MouseButtonRelease:
                play("click")
            elif event.type() == QEvent.Type.KeyRelease:
                key = event.key()
                if key in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    play("click")

        if isinstance(obj, ToggleSwitch) and event.type() == QEvent.Type.MouseButtonRelease:
            play("toggle")

        return super().eventFilter(obj, event)


def install_button_filter():
    global _sound_filter
    app = QApplication.instance()
    if app and _sound_filter is None:
        _sound_filter = _GlobalSoundFilter(app)
        app.installEventFilter(_sound_filter)
        logger.info("Global sound filter installed")


def uninstall_button_filter():
    global _sound_filter
    if _sound_filter is None:
        return
    app = QApplication.instance()
    if app:
        app.removeEventFilter(_sound_filter)
    _sound_filter.setParent(None)
    _sound_filter = None
    logger.info("Global sound filter removed")
