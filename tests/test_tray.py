"""Tests for the system tray icon (show/hide) behaviour in the main window."""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from niruvi.config import _settings


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _make_manager(qapp, monkeypatch):
    """Build the main window with the persistent tray stubbed out."""
    from niruvi.ui.manager import Niruvi

    monkeypatch.setattr("niruvi.ui.manager.QSystemTrayIcon.isSystemTrayAvailable", lambda: True)
    manager = Niruvi()
    qapp.processEvents()
    return manager


def test_tray_created_when_enabled(qapp, monkeypatch):
    _settings["tray_enabled"] = True
    manager = _make_manager(qapp, monkeypatch)
    try:
        assert manager._tray is not None
        assert manager._tray.isVisible()
    finally:
        manager._really_quit = True
        manager.close()


def test_tray_hidden_when_disabled(qapp, monkeypatch):
    _settings["tray_enabled"] = True
    manager = _make_manager(qapp, monkeypatch)
    try:
        assert manager._tray is not None
        _settings["tray_enabled"] = False
        manager._sync_tray()
        qapp.processEvents()
        assert manager._tray is not None
        assert not manager._tray.isVisible()
    finally:
        manager._really_quit = True
        manager.close()


def test_tray_not_created_when_disabled_at_startup(qapp, monkeypatch):
    _settings["tray_enabled"] = False
    manager = _make_manager(qapp, monkeypatch)
    try:
        assert manager._tray is None
    finally:
        manager._really_quit = True
        manager.close()


def test_tray_reshown_when_re_enabled(qapp, monkeypatch):
    _settings["tray_enabled"] = True
    manager = _make_manager(qapp, monkeypatch)
    try:
        assert manager._tray is not None
        _settings["tray_enabled"] = False
        manager._sync_tray()
        qapp.processEvents()
        assert not manager._tray.isVisible()
        _settings["tray_enabled"] = True
        manager._sync_tray()
        qapp.processEvents()
        assert manager._tray.isVisible()
    finally:
        manager._really_quit = True
        manager.close()
