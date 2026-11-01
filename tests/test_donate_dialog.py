"""Tests for the donation dialog wiring and UPI payment details."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QDialog, QPushButton

from niruvi.constants import RAZORPAY_URL, SUPPORT_URL, UPI_ID


@pytest.fixture
def dialog(qapp):
    from niruvi.ui.donate_dialog import DonateDialog
    from niruvi.utils.theme_engine import init_theme

    init_theme(qapp)
    dlg = DonateDialog()
    yield dlg
    dlg.deleteLater()


class TestDonationDetails:
    def test_payment_identifiers_match_requested_values(self):
        assert SUPPORT_URL == "https://ko-fi.com/putinservai"
        assert RAZORPAY_URL == "https://razorpay.me/@putin"
        assert UPI_ID == "putinservai-1@okhdfcbank"

    def test_upi_uri_is_embeddable(self):
        from niruvi.utils import qr

        uri = qr.upi_uri(UPI_ID, "Niruvi")
        assert uri.startswith("upi://pay?")
        assert "putinservai-1%40okhdfcbank" in uri


class TestDonateDialog:
    def test_constructs_with_both_payment_links(self, dialog):
        labels = _all_button_text(dialog)
        assert "Open Ko-fi" in labels
        assert "Open Razorpay" in labels

    def test_offers_upi_copy_action(self, dialog):
        assert "Copy UPI ID" in _all_button_text(dialog)

    def test_shows_the_upi_id(self, dialog):
        assert any(UPI_ID in text for text in _all_label_text(dialog))

    def test_renders_a_scannable_qr_pixmap(self, dialog):
        from PyQt6.QtWidgets import QLabel

        pixmaps = [lbl.pixmap() for lbl in dialog.findChildren(QLabel) if not lbl.pixmap().isNull()]
        assert pixmaps, "expected at least one rendered pixmap (the UPI QR)"
        # The QR label must be square and comfortably scannable.
        assert any(p.width() == p.height() >= 100 for p in pixmaps)

    def test_copy_upi_id_writes_to_clipboard(self, dialog, qapp):
        _find_button(dialog, "Copy UPI ID").click()
        assert qapp.clipboard().text().strip() == UPI_ID

    def test_links_open_in_the_system_browser(self, dialog, monkeypatch):
        opened = []
        monkeypatch.setattr(
            "niruvi.ui.donate_dialog.QDesktopServices.openUrl",
            lambda url: opened.append(url.toString()) or True,
        )
        _find_button(dialog, "Open Ko-fi").click()
        _find_button(dialog, "Open Razorpay").click()
        assert opened == [SUPPORT_URL, RAZORPAY_URL]

    def test_close_button_rejects(self, dialog):
        dialog.show()
        _find_button(dialog, "Close").click()
        assert dialog.result() != QDialog.DialogCode.Accepted

    def test_qr_failure_degrades_to_text(self, dialog, monkeypatch):
        """A broken QR must not take the whole dialog down."""
        monkeypatch.setattr(
            "niruvi.ui.donate_dialog.qr_encoder.encode",
            lambda *a, **k: (_ for _ in ()).throw(__import__("niruvi.utils.qr", fromlist=["x"]).QrCodeError("boom")),
        )
        replacement = type(dialog)()
        assert UPI_ID in _all_label_text(replacement)
        replacement.deleteLater()


def _find_button(dialog, text):
    for button in dialog.findChildren(QPushButton):
        if button.text().strip() == text:
            return button
    raise AssertionError(f"button {text!r} not found")


def _all_button_text(dialog):
    return {b.text().strip() for b in dialog.findChildren(QPushButton)}


def _all_label_text(dialog):
    from PyQt6.QtWidgets import QLabel

    return {lbl.text() for lbl in dialog.findChildren(QLabel)}
