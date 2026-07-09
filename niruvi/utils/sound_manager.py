"""Sound effects system for Niruvi UI.

Provides fire-and-forget audio playback using QSoundEffect (Qt6 Multimedia)
as primary backend, with subprocess fallback for non-Qt environments.
Category-based toggles and per-category volume for fine-grained control.
"""

import logging
import math
import os
import shutil
import struct
import subprocess
import tempfile
import time
import wave
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, QTimer, QUrl
from PyQt6.QtWidgets import QMenu

if TYPE_CHECKING:
    from PyQt6.QtMultimedia import QSoundEffect

from niruvi.config import _settings

logger = logging.getLogger(__name__)

_SOUND_MAP: dict[str, str] = {
    "click": "click-button-app-147358.wav",
    "toggle": "click-menu-app-147357.wav",
    "success": "new-information-153314.wav",
    "error": "error-in-the-file-system-149908.wav",
    "warning": "system-error-notice-132470.wav",
    "interface": "interface-2-126517.wav",
    "navigation": "navigation-sound-1-269298.wav",
    "info": "message-incoming-02-199577.wav",
    "notification": "notification-beep-229154.wav",
    "progress": "new-notification-07-210334.wav",
    "drop": "message-incoming-132126.wav",
}

_SOUND_CATEGORIES: dict[str, str] = {
    "click": "feedback",
    "toggle": "feedback",
    "success": "feedback",
    "error": "feedback",
    "warning": "feedback",
    "drop": "feedback",
    "progress": "feedback",
    "interface": "navigation",
    "navigation": "navigation",
    "info": "notifications",
    "notification": "notifications",
}

_CATEGORY_SETTINGS_KEYS: dict[str, str] = {
    "feedback": "sound_feedback_enabled",
    "navigation": "sound_navigation_enabled",
    "notifications": "sound_notifications_enabled",
}

_CATEGORY_VOLUME_KEYS: dict[str, str] = {
    "feedback": "sound_volume_feedback",
    "navigation": "sound_volume_navigation",
    "notifications": "sound_volume_notifications",
}

_PLAYER_PRIORITY = [
    "paplay",
    "pw-play",
    "canberra-gtk-play",
    "play",
    "ffplay",
    "aplay",
    "gst-play-1.0",
    "cvlc",
    "mpg123",
]

_VOLUME_PLAYERS: dict[str, str] = {
    "paplay": "--volume",
    "pw-play": "--volume",
    "ffplay": "-volume",
}

_audio_dir: str = ""
_player: str | None = None
_initialized = False
_last_play_time: float = 0.0
_PLAY_DEBOUNCE_S: float = 0.3

_fallback_wav: str | None = None
_subprocess_cleanup_timer: QTimer | None = None
_spawned_processes: list[subprocess.Popen] = []


def _generate_fallback_wav() -> str:
    """Generate a simple sine-wave beep WAV file for when no audio files exist."""
    global _fallback_wav
    if _fallback_wav and os.path.exists(_fallback_wav):
        return _fallback_wav
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        _fallback_wav = tmp.name
        tmp.close()
        sample_rate = 44100
        duration = 0.15
        frequency = 660.0
        num_samples = int(sample_rate * duration)
        data = b""
        for i in range(num_samples):
            t = i / sample_rate
            value = int(16000 * (1.0 - t / duration) * math.sin(2 * 3.14159 * frequency * t))
            data += struct.pack("<h", value)
        with wave.open(_fallback_wav, "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(data)
        return _fallback_wav
    except Exception as e:
        logger.debug("Failed to generate fallback WAV: %s", e)
        return ""


def _validate_audio_file(path: str) -> bool:
    """Quick header magic check to validate audio file."""
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "rb") as f:
            header = f.read(12)
        if header.startswith(b"RIFF") and header[8:12] == b"WAVE":
            return True
        if header.startswith(b"\xff\xfb") or header.startswith(b"\xff\xf3") or header.startswith(b"\xff\xf2"):
            return True
        if header.startswith(b"ID3") or header[:3] == b"\x00\x00\x00":
            return True
        if header.startswith(b"OggS"):
            return True
        with open(path, "rb") as f:
            f.seek(0)
            remaining = f.read(64)
        if b"Lavf" in remaining or b"libav" in remaining or b"ffmpeg" in remaining:
            return True
        logger.debug("Unknown audio format for %s (header: %r)", path, header[:8])
        return False
    except OSError as e:
        logger.debug("Cannot validate %s: %s", path, e)
        return False


def _find_audio_dir() -> str:
    from niruvi.config import INSTALLED_DIR, get_data_dir

    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    candidates: list[str] = []
    appdir = os.environ.get("APPDIR")
    if appdir:
        candidates.append(os.path.join(appdir, "asset", "audio"))
    candidates.append(os.path.join(_root, "asset", "audio"))
    data_dir = get_data_dir()
    candidates.append(os.path.join(data_dir, "asset", "audio"))
    inst = os.environ.get("APPIMAGE", "")
    if inst:
        candidates.append(os.path.join(os.path.dirname(inst), "asset", "audio"))
    candidates.append(os.path.join(INSTALLED_DIR, "asset", "audio"))
    seen = set()
    for path in candidates:
        norm = os.path.normpath(os.path.abspath(path))
        if norm in seen:
            continue
        seen.add(norm)
        if os.path.isdir(norm):
            return norm
    logger.debug("audio directory not found, checked: %s", candidates)
    return ""


def _init():
    global _audio_dir, _player, _initialized, _subprocess_cleanup_timer
    if _initialized:
        return
    _audio_dir = _find_audio_dir()
    _try_init_qsound()
    if not _player:
        for p in _PLAYER_PRIORITY:
            found = shutil.which(p)
            if found:
                _player = p
                break
    _initialized = True
    if not _player and not _audio_dir:
        _generate_fallback_wav()
    if not _player:
        logger.info("No audio player found (tried: %s) — using fallback beep", _PLAYER_PRIORITY)
    if _subprocess_cleanup_timer is None:
        _subprocess_cleanup_timer = QTimer()
        _subprocess_cleanup_timer.timeout.connect(_cleanup_zombies)
        _subprocess_cleanup_timer.start(30000)


def _try_init_qsound():
    """Try to initialize QSoundEffect as primary backend."""
    global _player
    try:
        from PyQt6.QtMultimedia import QSoundEffect

        effect = QSoundEffect()
        if effect is not None and effect.status() != QSoundEffect.Status.Error:
            _player = "qsound"
            return
    except Exception:
        pass


class _SoundEffectPlayer(QObject):
    """Global singleton QSoundEffect player with lazy init."""

    def __init__(self):
        super().__init__()
        self._effect: QSoundEffect | None = None
        self._current_source: str = ""

    def play(self, source: str, volume: float):
        if self._effect is None:
            try:
                from PyQt6.QtMultimedia import QSoundEffect

                self._effect = QSoundEffect(self)
                self._effect.setLoopCount(1)
            except Exception:
                return
        if source != self._current_source:
            self._effect.setSource(QUrl.fromLocalFile(source))
            self._current_source = source
        self._effect.setVolume(volume)
        self._effect.play()


_qsound_player: _SoundEffectPlayer | None = None


def _get_qsound() -> _SoundEffectPlayer | None:
    global _qsound_player
    if _qsound_player is None:
        _qsound_player = _SoundEffectPlayer()
    return _qsound_player


def play(sound_name: str):
    global _last_play_time
    if not _settings.get("sound_effects_enabled", True):
        return
    category = _SOUND_CATEGORIES.get(sound_name, "feedback")
    category_key = _CATEGORY_SETTINGS_KEYS.get(category)
    if category_key and not _settings.get(category_key, True):
        return
    now = time.time()
    if now - _last_play_time < _PLAY_DEBOUNCE_S:
        return
    _last_play_time = now
    _init()
    if not _player and not _audio_dir and _fallback_wav:
        _play_subprocess_fallback()
        return
    filename = _SOUND_MAP.get(sound_name)
    if not filename:
        return
    filepath = os.path.join(_audio_dir, filename) if _audio_dir else ""
    if not filepath or not os.path.isfile(filepath):
        if _fallback_wav:
            _play_subprocess_fallback()
        return
    if not _validate_audio_file(filepath):
        logger.debug("invalid audio file, falling back: %s", filepath)
        if _fallback_wav:
            _play_subprocess_fallback()
        return

    vol = _settings.get("sound_volume", 0.7)
    category_vol_key = _CATEGORY_VOLUME_KEYS.get(category)
    if category_vol_key:
        cat_vol = _settings.get(category_vol_key)
        if cat_vol is not None:
            vol = vol * cat_vol

    if _player == "qsound":
        qs = _get_qsound()
        if qs:
            qs.play(filepath, vol)
        return

    vol_flag = _VOLUME_PLAYERS.get(_player)
    try:
        if _player == "ffplay":
            vol_int = max(0, min(100, int(vol * 100)))
            args = [_player, "-nodisp", "-autoexit", "-loglevel", "quiet", "-volume", str(vol_int), filepath]
            _spawn(args)
        elif _player == "paplay":
            vol_val = str(max(0, min(65535, int(vol * 65535))))
            _spawn([_player, vol_flag, vol_val, filepath])
        elif _player == "pw-play":
            vol_val = f"{vol:.2f}"
            _spawn([_player, vol_flag, vol_val, filepath])
        elif _player == "canberra-gtk-play":
            vol_val = str(int(vol * 100))
            _spawn([_player, "--file", filepath, "--volume", vol_val])
        elif _player == "cvlc":
            _spawn([_player, "--play-and-exit", "--no-video", filepath])
        else:
            _spawn([_player, filepath])
    except Exception as e:
        logger.debug("failed to play sound '%s': %s", sound_name, e, exc_info=True)


def _spawn(args: list[str]):
    try:
        p = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _spawned_processes.append(p)
    except Exception:
        pass


def _play_subprocess_fallback():
    """Play the fallback WAV via aplay/paplay."""
    global _fallback_wav
    path = _fallback_wav or _generate_fallback_wav()
    if not path or not os.path.isfile(path):
        return
    for player in ("paplay", "aplay", "ffplay"):
        exe = shutil.which(player)
        if exe:
            try:
                if player == "ffplay":
                    subprocess.Popen(
                        [exe, "-nodisp", "-autoexit", "-loglevel", "quiet", path],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    subprocess.Popen(
                        [exe, path],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                return
            except Exception:
                continue


def _cleanup_zombies():
    global _spawned_processes
    alive = []
    for p in _spawned_processes:
        if p.poll() is None:
            alive.append(p)
            try:
                p.kill()
            except Exception:
                pass
    _spawned_processes = alive


def cleanup():
    """Call on app exit to kill orphaned player processes."""
    global _subprocess_cleanup_timer
    if _subprocess_cleanup_timer:
        _subprocess_cleanup_timer.stop()
        _subprocess_cleanup_timer = None
    for p in _spawned_processes:
        try:
            p.kill()
        except Exception:
            pass
    _spawned_processes.clear()


def install_menu_sound(menu: QMenu):
    try:
        menu.triggered.connect(lambda: play("click"))
    except Exception as e:
        logger.debug("Failed to install menu sound: %s", e, exc_info=True)


class _GlobalSoundFilterPlaceholder(QObject):
    def eventFilter(self, obj, event):
        return super().eventFilter(obj, event)


def install_button_filter():
    pass


def uninstall_button_filter():
    pass
