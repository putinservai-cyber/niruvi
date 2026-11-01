"""Tests for the Settings dialog layout and controls."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QDialogButtonBox, QGroupBox


@pytest.fixture
def dialog(qapp):
    from niruvi.ui.settings import SettingsDialog
    from niruvi.utils.theme_engine import init_theme

    init_theme(qapp)
    dlg = SettingsDialog()
    yield dlg
    dlg.close()
    dlg.deleteLater()


class TestSettingsDialog:
    def test_has_ok_and_cancel_buttons(self, dialog):
        box = dialog.findChild(QDialogButtonBox)
        assert box is not None
        assert box.button(QDialogButtonBox.StandardButton.Ok) is not None
        assert box.button(QDialogButtonBox.StandardButton.Cancel) is not None

    def test_buttons_are_outside_the_scroll_area(self, dialog):
        box = dialog.findChild(QDialogButtonBox)
        page = dialog._page
        # The button box must not be a descendant of the scrolling content.
        parent = box.parent()
        while parent is not None:
            assert parent is not page._scroll
            parent = parent.parent()

    def test_no_horizontal_scrollbar_at_minimum_size(self, dialog, qapp):
        dialog.resize(dialog.minimumWidth(), dialog.minimumHeight())
        dialog.show()
        qapp.processEvents()
        assert dialog._page._scroll.horizontalScrollBar().maximum() == 0

    def test_grouped_sections_are_present(self, dialog):
        titles = {g.title() for g in dialog.findChildren(QGroupBox)}
        assert "Installation defaults" in titles
        assert "Process Isolation" in titles
        assert "Privacy" in titles

    def test_fits_on_a_1366x768_screen(self, dialog):
        assert dialog.minimumWidth() <= 700
        assert dialog.minimumHeight() <= 520
