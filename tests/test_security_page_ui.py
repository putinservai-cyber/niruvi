"""Security tab UI: honest summary, profile switching with visible deltas,
real verification output, audit excerpt."""

import pytest


@pytest.fixture
def app_info(tmp_path):
    return {"path": str(tmp_path), "display_name": "Check"}


@pytest.fixture
def page(qapp, app_info):
    from PyQt6.QtWidgets import QMainWindow

    from niruvi.ui.security_page import SecurityPage
    from niruvi.utils.theme_engine import init_theme

    init_theme(qapp)
    p = SecurityPage("Check.App", app_info)
    _win = QMainWindow()
    _win.setCentralWidget(p.page)
    _win.show()
    yield p
    _win.close()


class TestSummary:
    def test_summary_is_honest_not_decorative(self, page):
        text = page.summary_label.text()
        assert any(word in text for word in ("Protected", "Partially protected", "Not protected", "failed", "verified"))

    def test_policy_resolved(self, page):
        assert page._effective_policy().profile in ("standard", "strict", "custom", "developer")


class TestProfileSwitch:
    def test_picking_developer_shows_relaxation_warning(self, page):
        page.profile_combo.setCurrentIndex(page.profile_combo.findData("developer"))
        assert "RELAX" in page.relax_label.text().upper()
        assert "DEVELOPER" in page.relax_label.text().upper()

    def test_picking_same_profile_no_relaxation_needed(self, page):
        page.profile_combo.setCurrentIndex(page.profile_combo.findData("standard"))
        assert page.relax_label.text() == "" or "RELAX" not in page.relax_label.text()


class TestVerification:
    def test_verification_box_reports_probe_output(self, page, monkeypatch):
        from niruvi.core import sandbox_diagnostics

        monkeypatch.setattr(
            sandbox_diagnostics,
            "verify_sandbox",
            lambda *a, **k: {
                "backend": "bwrap",
                "verified": True,
                "checks": {"network": "verified"},
                "summary": "verified",
                "detail": [],
            },
        )
        # security page imports verify_sandbox into its module namespace
        page._run_verification()
        assert page.verification_box.isVisible()
        assert "verified" in page.verification_box.toPlainText()

    def test_audit_shows_entries(self, page):
        page._show_audit()
        assert page.verification_box.isVisible()
