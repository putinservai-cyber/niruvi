"""Donation dialog: Ko-fi, Razorpay and UPI (QR scan or copy the VPA).

Kept deliberately dependency-free - the UPI QR matrix is produced by
``niruvi.utils.qr`` and painted straight into a ``QLabel`` pixmap.
"""

from __future__ import annotations

import logging
import webbrowser

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from niruvi._version import __app_name__
from niruvi.constants import RAZORPAY_URL, SUPPORT_URL, UPI_ID
from niruvi.utils import get_icon, icon_pixmap
from niruvi.utils import qr as qr_encoder
from niruvi.utils.sound_manager import play as play_sound
from niruvi.utils.sound_manager import play_and
from niruvi.utils.theme_engine import style

logger = logging.getLogger(__name__)

_QR_MODULE_PX = 6
# The QR spec requires a four-module quiet zone; phone cameras crop to the
# finder patterns, so anything less reliably fails to scan.
_QR_QUIET_MODULES = 4


class DonateDialog(QDialog):
    """Modal donation chooser with a scannable UPI QR code."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Support {__app_name__}")
        self.setModal(True)
        self.setMinimumSize(460, 520)
        self._init_ui()

    # ── construction ────────────────────────────────────────────────────────

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        layout.addWidget(self._build_header())
        layout.addWidget(self._build_methods())
        layout.addWidget(self._build_upi_card())
        layout.addStretch(1)
        layout.addWidget(self._build_footer())

    def _build_header(self) -> QWidget:
        header = QWidget()
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        icon = QLabel()
        icon.setPixmap(icon_pixmap(get_icon("emblem-favorite-symbolic", "starred"), 40))
        icon.setFixedSize(40, 40)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(icon)

        text = QVBoxLayout()
        text.setSpacing(2)
        title = QLabel(f"<b>Support {__app_name__} development</b>")
        subtitle = QLabel(
            "Your support keeps bug fixes coming and helps improve AppImage\ncompatibility on more distributions."
        )
        subtitle.setStyleSheet(style("color: {colors.muted};"))
        text.addWidget(title)
        text.addWidget(subtitle)
        row.addLayout(text, 1)
        return header

    def _build_methods(self) -> QWidget:
        panel = QFrame()
        panel.setStyleSheet(
            style("QFrame {background: {colors.surface};border: 1px solid {colors.border};border-radius: 10px;}")
        )
        grid = QVBoxLayout(panel)
        grid.setContentsMargins(14, 14, 14, 14)
        grid.setSpacing(10)

        grid.addWidget(
            self._method_row(
                "emblem-favorite-symbolic",
                "Ko-fi",
                "One-time or monthly, no account needed",
                "Open Ko-fi",
                SUPPORT_URL,
                variant="primary",
            )
        )
        grid.addWidget(
            self._method_row(
                "wallet-symbolic",
                "Razorpay",
                "UPI, cards, netbanking and wallets",
                "Open Razorpay",
                RAZORPAY_URL,
                variant="default",
            )
        )
        return panel

    def _method_row(self, icon_name, title, subtitle, button_text, url, variant) -> QWidget:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(12)

        icon = QLabel()
        icon.setPixmap(icon_pixmap(get_icon(icon_name), 24))
        icon.setFixedSize(24, 24)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row_layout.addWidget(icon)

        text = QVBoxLayout()
        text.setSpacing(0)
        text.addWidget(QLabel(f"<b>{title}</b>"))
        hint = QLabel(subtitle)
        hint.setStyleSheet(style("color: {colors.muted}; font-size: 11px;"))
        text.addWidget(hint)
        row_layout.addLayout(text, 1)

        button = QPushButton(button_text)
        button.setProperty("variant", variant)
        button.setIcon(get_icon("go-next-symbolic", "emblem-favorite-symbolic"))
        button.clicked.connect(lambda _=False, target=url: self._open_link(target))
        row_layout.addWidget(button)
        return row

    def _build_upi_card(self) -> QWidget:
        panel = QFrame()
        panel.setStyleSheet(
            style("QFrame {background: {colors.surface};border: 1px solid {colors.border};border-radius: 10px;}")
        )
        outer = QHBoxLayout(panel)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(16)

        text = QVBoxLayout()
        text.setSpacing(4)
        heading = QLabel("<b>UPI</b>")
        hint = QLabel("Scan the code with any UPI app, or copy the ID.")
        hint.setWordWrap(True)
        hint.setStyleSheet(style("color: {colors.muted};"))
        id_label = QLabel(f"<code>{UPI_ID}</code>")
        id_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        id_label.setStyleSheet(style("font-family: monospace; color: {colors.subtle};"))

        copy_btn = QPushButton("Copy UPI ID")
        copy_btn.setProperty("variant", "default")
        copy_btn.setIcon(get_icon("edit-copy-symbolic", "clipboard-copy"))
        copy_btn.clicked.connect(self._copy_upi_id)

        text.addWidget(heading)
        text.addWidget(hint)
        text.addWidget(id_label)
        text.addWidget(copy_btn)
        text.addStretch(1)
        outer.addLayout(text, 1)

        outer.addWidget(self._build_qr_label())
        return panel

    def _build_qr_label(self) -> QLabel:
        label = QLabel()
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setToolTip(f"Scan to pay {UPI_ID}")

        pixmap = self._render_qr()
        if pixmap is not None:
            label.setPixmap(pixmap)
            label.setFixedSize(pixmap.size())
        else:
            # Degrade to the plain ID rather than leaving an empty box.
            label.setText(UPI_ID)
            label.setStyleSheet(style("color: {colors.subtle}; padding: 12px;"))
        return label

    def _render_qr(self) -> QPixmap | None:
        """Render the UPI intent link; returns None if it cannot be built."""
        try:
            payload = qr_encoder.upi_uri(UPI_ID, __app_name__, note=f"Support {__app_name__}")
            matrix = qr_encoder.encode(payload)
        except qr_encoder.QrCodeError as exc:
            logger.warning("Could not build UPI QR code: %s", exc)
            return None

        image = qr_encoder.to_image(matrix, module_px=_QR_MODULE_PX, quiet_modules=_QR_QUIET_MODULES)
        if image is None:
            return None
        return QPixmap.fromImage(image)

    def _build_footer(self) -> QWidget:
        footer = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        footer.rejected.connect(lambda: play_and("navigation", self.reject))
        return footer

    # ── actions ─────────────────────────────────────────────────────────────

    def _open_link(self, url: str) -> None:
        play_sound("interface")
        if not QDesktopServices.openUrl(QUrl(url)):
            webbrowser.open(url)

    def _copy_upi_id(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(UPI_ID)
        play_sound("success")
