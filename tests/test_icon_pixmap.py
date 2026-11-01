"""Tests for DPR-aware icon rendering.

``QIcon.pixmap(w, h)`` ignores the screen's device pixel ratio, which makes
icons blurry on HiDPI displays. ``icon_pixmap()`` renders at ``size * dpr``
physical pixels and tags the pixmap so Qt downsamples it for display.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QIcon, QPixmap

from niruvi.utils import icon_pixmap


def _icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill()
    return QIcon(pixmap)


class TestIconPixmap:
    def test_renders_at_requested_device_pixel_ratio(self, qapp):
        pm = icon_pixmap(_icon(), 32, dpr=2.0)
        assert not pm.isNull()
        # The backing store is scaled by the DPR; the logical size stays 32.
        assert pm.width() == 64
        assert pm.devicePixelRatio() == 2.0
        assert pm.deviceIndependentSize().toSize() == QSize(32, 32)

    def test_dpr_one_is_unscaled(self, qapp):
        pm = icon_pixmap(_icon(), 24, dpr=1.0)
        assert pm.width() == 24
        assert pm.devicePixelRatio() == 1.0

    def test_null_icon_returns_null_pixmap(self, qapp):
        pm = icon_pixmap(QIcon(), 32, dpr=2.0)
        assert pm.isNull()

    def test_invalid_dpr_falls_back_to_one(self, qapp):
        pm = icon_pixmap(_icon(), 16, dpr=0)
        assert not pm.isNull()
        assert pm.devicePixelRatio() == 1.0


class TestScreenDpr:
    def test_reports_positive_ratio(self, qapp):
        from niruvi.desktop.icon_utils import screen_dpr

        assert screen_dpr() > 0
