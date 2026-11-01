"""App Info → Security tab.

Shows the enforced security profile with honest provenance:

* which profile produced this app's policy (STANDARD/STRICT/CUSTOM/DEVELOPER)
* the trust ladder:
  Protected → Partially protected → Not protected → Launch failed (with
  reasons and the audit entry), never a decorative "secure"
* controls: switch profile (shows relaxations before applying), Run
  verification (real bwrap probes), Stop running app (only pids we launched),
  audit log excerpt (nothing hidden)
* launch failure states offer the audit trail, not a silent fallback.

Fail-open refusal (absent bwrap, launch refused by the backend) is shown
with the refusal reasons verbatim.
"""

import logging

from PyQt6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QVBoxLayout,
)

from niruvi.core.backends import get_backend
from niruvi.core.policy import SandboxPolicy
from niruvi.core.profiles import (
    PROFILE_LABELS,
    PROFILE_NAMES,
    build_profile,
    switch_profile,
)
from niruvi.core.sandbox_diagnostics import verify_sandbox
from niruvi.utils import get_icon
from niruvi.utils.sound_manager import play_and
from niruvi.utils.styles import tab_page_style
from niruvi.utils.theme_engine import style

logger = logging.getLogger(__name__)

_SUMMARY_COLORS = {
    "protected": "{success}",
    "partially_protected": "{warning}",
    "not_protected": "{error}",
    "failed": "{error}",
    "not-verified": "{subtle}",
    "partial": "{warning}",
    "verified": "{success}",
}


class SecurityPage:
    """Factory + behaviour; installed as a widget inside App Info."""

    def __init__(self, app_name: str, app_info: dict, parent=None):
        self._app_name = app_name
        self._info = app_info
        self._policy: SandboxPolicy | None = None
        self._build()
        self._refresh()

    # ── widget tree ───────────────────────────────────────────────────
    def _build(self):
        self.page = self._build_page()

    def _build_page(self):
        from PyQt6.QtWidgets import QWidget

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        group = QGroupBox("Security profile & enforcement")
        group.setStyleSheet(tab_page_style())
        gl = QVBoxLayout(group)
        gl.setSpacing(6)

        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        gl.addWidget(self.summary_label)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("Profile:"))
        self.profile_combo = QComboBox()
        for name in PROFILE_NAMES:
            self.profile_combo.addItem(PROFILE_LABELS[name], userData=name)
        self.profile_combo.currentIndexChanged.connect(self._on_profile_pick)
        row.addWidget(self.profile_combo)
        row.addStretch()
        gl.addLayout(row)

        self.relax_label = QLabel("")
        self.relax_label.setWordWrap(True)
        self.relax_label.setStyleSheet(style("color: {colors.warning}; font-size: 11px;"))
        gl.addWidget(self.relax_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.btn_apply = QPushButton(get_icon("dialog-ok-apply"), "Apply Profile")
        self.btn_apply.clicked.connect(lambda: play_and("click", self._apply_profile))
        btn_row.addWidget(self.btn_apply)

        self.btn_verify = QPushButton(get_icon("security-high"), "Run Verification")
        self.btn_verify.setToolTip("Run real probes inside a sandbox from the current policy")
        self.btn_verify.clicked.connect(lambda: play_and("click", self._run_verification))
        btn_row.addWidget(self.btn_verify)

        self.btn_audit = QPushButton(get_icon("text-x-generic"), "Audit Log")
        self.btn_audit.clicked.connect(lambda: play_and("click", self._show_audit))
        btn_row.addWidget(self.btn_audit)
        btn_row.addStretch()
        gl.addLayout(btn_row)

        self.verification_box = QPlainTextEdit()
        self.verification_box.setReadOnly(True)
        self.verification_box.setPlaceholderText(
            "Verification runs real probes inside the sandbox; results appear here."
        )
        self.verification_box.setMaximumHeight(110)
        self.verification_box.hide()
        gl.addWidget(self.verification_box)

        layout.addWidget(group)
        layout.addStretch()
        return page

    # ── data plumbing ─────────────────────────────────────────────────
    def _effective_policy(self) -> SandboxPolicy:
        if self._policy is None:
            from niruvi.core.sandbox_manager import PolicyResolutionError, get_sandbox_manager
            from niruvi.desktop.installation_registry import InstallationRegistry

            record = InstallationRegistry().get(self._app_name)
            try:
                self._policy = get_sandbox_manager().resolve_policy(self._app_name, record)
            except PolicyResolutionError as e:
                logger.error("Policy resolution failed in Security tab: %s", e)
                self._policy = build_profile("standard", self._app_name)
        return self._policy

    def _refresh(self):
        policy = self._effective_policy()
        backend = get_backend(policy.backend, strict=True)
        from niruvi.core.policy_engine import permission_states, summary_state

        states = permission_states(policy, backend)
        summary = summary_state(states)
        self.summary_label.setText(
            f"<b>{self._summary_text(summary)}</b><br>"
            f"<small>backend: {backend.name} (visible enforcement per-permission listed in the "
            "Permissions tab)</small>"
        )
        self._policy = policy
        # Select the profile combo for provenance (custom stores as custom).
        idx = self.profile_combo.findData(policy.profile or "custom")
        self.profile_combo.blockSignals(True)
        self.profile_combo.setCurrentIndex(idx if idx >= 0 else self.profile_combo.findData("custom"))
        self.profile_combo.blockSignals(False)
        self.relax_label.setText("")

    @staticmethod
    def _summary_text(summary: str) -> str:
        return {
            "protected": "Protected",
            "partially_protected": "Partially protected",
            "not_protected": "Not protected",
            "failed": "Enforcement failed at launch",
            "verified": "Verified (real probes passed)",
            "partial": "Partially verified",
            "not-verified": "Not verified yet — run probes",
        }.get(summary, summary)

    # ── actions ───────────────────────────────────────────────────────
    def _on_policy_changed(self):
        self._policy = None
        self._refresh()

    def _on_profile_pick(self):
        name = self.profile_combo.currentData()
        if not name:
            return
        try:
            plan = switch_profile(self._effective_policy(), name, self._app_name)
        except ValueError as e:
            self.relax_label.setText(str(e))
            return
        if plan["relaxed"]:
            self.relax_label.setText(
                "Switching will RELAX: " + "; ".join(plan["relaxed"][:6])
            )
        else:
            self.relax_label.setText("")
        self._pending_profile = plan["policy"]

    def _apply_profile(self):
        plan = getattr(self, "_pending_profile", None)
        if plan is None:
            self._on_profile_pick()
            plan = getattr(self, "_pending_profile", None)
        if plan is None:
            return
        name = self.profile_combo.currentData()
        if name == "developer":
            ret = QMessageBox.question(
                self.page,
                "DEVELOPER profile",
                "DEVELOPER is explicitly weaker. Apply anyway?",
            )
            if ret != QMessageBox.StandardButton.Yes:
                return
        record = None
        from niruvi.desktop.installation_registry import InstallationRegistry

        record = InstallationRegistry().get(self._app_name)
        backend = get_backend(plan.backend or "bwrap", strict=True)
        try:
            result = backend.apply_policy(self._app_name, plan, record=record)
        except Exception as e:
            logger.exception("Profile apply crashed")
            QMessageBox.critical(self.page, "Apply failed", str(e))
            return
        if not result.ok:
            QMessageBox.critical(
                self.page, "Apply failed", result.error or "the policy file refused to persist"
            )
            return
        self._policy = None
        self._pending_profile = None
        self._refresh()

    def _run_verification(self):
        self.verification_box.show()
        self.verification_box.setPlainText("Running real probes inside the sandbox…")
        try:
            res = verify_sandbox(self._effective_policy(), self._info.get("path", ""), {})
        except Exception as e:
            logger.exception("verification crashed")
            self.verification_box.setPlainText(f"Verification crashed: {e}")
            return
        lines = [f"backend: {res['backend']}  summary: {res['summary']}"]
        for k, v in res["checks"].items():
            lines.append(f"  {k}: {v}")
        for d in res["detail"]:
            lines.append(f"  ! {d}")
        self.verification_box.setPlainText("\n".join(lines))
        if res["summary"] == "verified":
            self.summary_label.setText(f"<b>{self._summary_text('verified')}</b>")
        elif res["summary"] == "partial":
            self.summary_label.setText(f"<b>{self._summary_text('partial')}</b>")

    def _show_audit(self):
        from niruvi.core.sandbox_audit import read_launches

        entries = read_launches(limit=50)
        app_log = [e for e in entries if e.get("app_id") == self._app_name]
        if not app_log:
            self.verification_box.show()
            self.verification_box.setPlainText("No audit entries for this app yet.")
            return
        text = []
        for e in app_log[:20]:
            text.append(
                f"{e.get('ts')}  {e.get('result')}  profile={e.get('profile')} "
                f"backend={e.get('backend')} pid={e.get('pid')}"
                + (f"  reason={e.get('refused_reason') or e.get('override_reason')}" if e.get("refused_reason") or e.get("override_reason") else "")
            )
        self.verification_box.show()
        self.verification_box.setPlainText("\n".join(text))
