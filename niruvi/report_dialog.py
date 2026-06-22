"""Error and problem report dialogs with detailed human-readable explanations."""

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon, QFont
from niruvi.utils import get_icon

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTextBrowser, QDialogButtonBox,
    QWidget, QTabWidget, QApplication,
)


def _run_capture(cmd: list[str], timeout=10) -> str:
    """Run a command and return its output, or an error message."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = result.stdout.strip()
        if result.stderr:
            out += "\nstderr:\n" + result.stderr.strip()
        return out or "(no output)"
    except FileNotFoundError:
        return f"Command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return "Command timed out"
    except Exception as e:
        return f"Error: {e}"


class ErrorReportDialog(QDialog):
    """Detailed, human-readable error report dialog.

    Shows what went wrong in plain language, suggests fixes,
    and provides technical details for advanced users.
    """

    def __init__(self, parent=None,
                 title="Something went wrong",
                 summary="",
                 details="",
                 suggestions=None,
                 technical="",
                 log_text=""):
        super().__init__(parent)
        self.setWindowTitle("Error Report")
        self.setMinimumSize(640, 480)
        self._title = title
        self._summary = summary
        self._details = details
        self._suggestions = suggestions or []
        self._technical = technical
        self._log_text = log_text
        self._init_ui()

    @classmethod
    def from_exception(cls, parent, exception: Exception, context: str = "",
                       log_text: str = "") -> "ErrorReportDialog":
        """Create a report from a Python exception with helpful context."""
        exc_type = type(exception).__name__
        exc_msg = str(exception)
        import traceback
        tb = traceback.format_exc()

        summary = f"An unexpected error occurred while {context or 'running the operation'}."
        details = str(exception)
        suggestions = _suggest_for_exception(exception, exc_type, context)
        technical = (
            f"Exception Type: {exc_type}\n"
            f"Message: {exc_msg}\n"
            f"Context: {context or 'N/A'}\n"
            f"\nTraceback:\n{tb}"
        )
        return cls(
            parent,
            title=f"Error: {exc_type}",
            summary=summary,
            details=details,
            suggestions=suggestions,
            technical=technical,
            log_text=log_text,
        )

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # ── Header with error icon ──
        header = QHBoxLayout()
        icon_label = QLabel()
        icon = get_icon("dialog-error")
        if not icon.isNull():
            icon_label.setPixmap(icon.pixmap(48, 48))
        icon_label.setFixedSize(48, 48)
        header.addWidget(icon_label)

        title_label = QLabel(f"<h2>{self._title}</h2>")
        title_label.setWordWrap(True)
        header.addWidget(title_label, 1)
        layout.addLayout(header)

        # ── Summary ──
        if self._summary:
            summary_label = QLabel(self._summary)
            summary_label.setWordWrap(True)
            summary_label.setStyleSheet(
                "color: palette(text); font-size: 11pt; margin-bottom: 4px;"
            )
            layout.addWidget(summary_label)

        # ── Details ──
        if self._details:
            details_label = QLabel(f"<b>What happened:</b>")
            layout.addWidget(details_label)
            details_text = QTextBrowser()
            details_text.setPlainText(self._details)
            details_text.setMaximumHeight(80)
            details_text.setStyleSheet("background: palette(base); border: 1px solid palette(mid);")
            layout.addWidget(details_text)

        # ── Suggestions ──
        if self._suggestions:
            sugg_label = QLabel("<b>Try these steps:</b>")
            layout.addWidget(sugg_label)
            for i, suggestion in enumerate(self._suggestions, 1):
                sugg_text = QLabel(f"  {i}.  {suggestion}")
                sugg_text.setWordWrap(True)
                sugg_text.setStyleSheet("color: palette(text); padding-left: 8px;")
                layout.addWidget(sugg_text)

        # ── Technical details (tabbed) ──
        tabs = QTabWidget()

        if self._technical:
            tech_browser = QTextBrowser()
            tech_browser.setPlainText(self._technical)
            tech_browser.setFont(QFont("monospace", 9))
            tabs.addTab(tech_browser, "Technical Details")

        if self._log_text:
            log_browser = QTextBrowser()
            log_browser.setPlainText(self._log_text)
            log_browser.setFont(QFont("monospace", 9))
            tabs.addTab(log_browser, "Build Log")

        # ── System info tab ──
        sys_browser = QTextBrowser()
        sys_browser.setPlainText(self._collect_system_info())
        sys_browser.setFont(QFont("monospace", 9))
        tabs.addTab(sys_browser, "System Info")

        layout.addWidget(tabs, 1)

        # ── Buttons ──
        btn_layout = QHBoxLayout()

        copy_btn = QPushButton(get_icon("edit-copy"), "Copy Report")
        copy_btn.clicked.connect(self._copy_report)
        btn_layout.addWidget(copy_btn)

        btn_layout.addStretch()

        close_btn = QPushButton(get_icon("dialog-close"), "Close")
        close_btn.clicked.connect(self.accept)
        close_btn.setStyleSheet("QPushButton { padding: 6px 20px; }")
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

    def _collect_system_info(self) -> str:
        """Collect relevant system information for debugging."""
        lines = []
        lines.append("=== System Information ===")
        lines.append(f"OS: {sys.platform}")
        try:
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        lines.append(f"Distro: {line.split('=', 1)[1].strip().strip('\"')}")
                        break
        except Exception:
            pass
        lines.append(f"Python: {sys.version}")
        lines.append(f"Python executable: {sys.executable}")
        lines.append(f"PyQt6: {_get_pyqt_version()}")
        lines.append(f"FUSE: {_check_fuse()}")
        lines.append(f"DISPLAY: {os.environ.get('DISPLAY', 'not set')}")
        lines.append(f"XDG_SESSION_TYPE: {os.environ.get('XDG_SESSION_TYPE', 'not set')}")
        lines.append(f"\n=== Environment ===")
        for key in ("HOME", "USER", "LANG", "PATH"):
            lines.append(f"{key}={os.environ.get(key, 'not set')}")
        lines.append(f"\n=== Build environment ===")
        lines.append(f"appimagetool: {_run_capture(['which', 'appimagetool-x86_64.AppImage'])}")
        lines.append(f"ar (deb): {_run_capture(['which', 'ar'])}")
        lines.append(f"rpm2cpio: {_run_capture(['which', 'rpm2cpio'])}")
        lines.append(f"tar: {_run_capture(['which', 'tar'])}")
        lines.append(f"cpio: {_run_capture(['which', 'cpio'])}")
        lines.append(f"zenity: {_run_capture(['which', 'zenity'])}")
        lines.append(f"kdialog: {_run_capture(['which', 'kdialog'])}")
        return "\n".join(lines)

    def _copy_report(self):
        """Copy a formatted report to the clipboard."""
        parts = [
            f"=== {self._title} ===",
            "",
            f"Summary: {self._summary}" if self._summary else "",
            f"Details: {self._details}" if self._details else "",
            "",
            "Suggestions:" if self._suggestions else "",
        ]
        for s in self._suggestions:
            parts.append(f"  - {s}")
        parts.append("")
        parts.append("--- Technical Details ---")
        if self._technical:
            parts.append(self._technical)
        if self._log_text:
            parts.append("")
            parts.append("--- Build Log ---")
            parts.append(self._log_text)
        parts.append("")
        parts.append("--- System Info ---")
        parts.append(self._collect_system_info())

        text = "\n".join(parts)
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        from PyQt6.QtWidgets import QToolTip
        QToolTip.showText(self.mapToGlobal(self.rect().center()), "Report copied to clipboard!", self)

    @staticmethod
    def suggest_for_build_error(error_msg: str) -> list[str]:
        """Return human-readable suggestions based on build error messages."""
        suggestions = []
        err_lower = error_msg.lower()

        if "not found" in err_lower or "no such file" in err_lower:
            suggestions.append("Make sure the source file exists and the path is correct.")
            suggestions.append("Check that you have read permission for the selected file.")
        if "permission" in err_lower or "denied" in err_lower:
            suggestions.append("Try running with appropriate permissions or check file ownership.")
            suggestions.append("Ensure the output directory is writable.")
        if "appimagetool" in err_lower:
            suggestions.append("Place appimagetool-x86_64.AppImage in the asset/ folder.")
            suggestions.append("Run 'chmod +x asset/appimagetool-x86_64.AppImage' to make it executable.")
        if "disk" in err_lower or "space" in err_lower or "no space" in err_lower:
            suggestions.append("Free up disk space using 'df -h' to check available space.")
            suggestions.append("Try a different output directory with more free space.")
        if "package" in err_lower or "extract" in err_lower:
            suggestions.append("Verify the package file is not corrupted. Try re-downloading it.")
            suggestions.append("Check that required tools are installed: ar, rpm2cpio, tar, cpio.")
        if "timeout" in err_lower or "timed out" in err_lower:
            suggestions.append("The operation took too long. Try with a smaller package or faster system.")
        if "fuse" in err_lower:
            suggestions.append("Install FUSE: 'sudo apt install fuse' or 'sudo dnf install fuse'.")
            suggestions.append("On some systems, try 'sudo modprobe fuse' first.")
        if not suggestions:
            suggestions.append("Check the Technical Details tab for more information about the error.")
            suggestions.append("Make sure all required tools and dependencies are installed.")
            suggestions.append("Try building with a different source package to isolate the issue.")
        return suggestions


def _get_pyqt_version() -> str:
    try:
        from PyQt6.QtCore import QT_VERSION_STR
        return QT_VERSION_STR
    except Exception:
        return "unknown"


def _check_fuse() -> str:
    try:
        r = subprocess.run(["mount", "-t", "fuse"], capture_output=True, text=True, timeout=5)
        if r.stdout.strip():
            return "mounted"
        r2 = subprocess.run(["lsmod"], capture_output=True, text=True, timeout=5)
        if "fuse" in r2.stdout:
            return "module loaded"
        return "not detected"
    except Exception:
        return "unknown"


def _suggest_for_exception(exc: Exception, exc_type: str, context: str) -> list[str]:
    """Generate human-readable suggestions from an exception."""
    msg = str(exc).lower()
    suggestions = []
    if isinstance(exc, FileNotFoundError):
        suggestions.append(f"The file or directory was not found. Double-check the path.")
        suggestions.append("Make sure the file exists and you have permission to access it.")
    elif isinstance(exc, PermissionError):
        suggestions.append("You don't have permission to access this file or directory.")
        suggestions.append("Try running with appropriate permissions or change file ownership.")
    elif isinstance(exc, subprocess.CalledProcessError):
        suggestions.append(f"A command failed with exit code {exc.returncode}. Check the log for details.")
        suggestions.append("Verify that all required tools are installed on your system.")
        suggestions.append("See the Technical Details tab for the exact command that failed.")
    elif isinstance(exc, subprocess.TimeoutExpired):
        suggestions.append("The operation timed out. Try with a smaller file or on a faster system.")
    elif isinstance(exc, OSError):
        suggestions.append("A system error occurred. This might be due to disk space, permissions, or a missing file.")
        suggestions.append("Check the Technical Details tab for more information.")
    elif "memory" in msg:
        suggestions.append("Your system may be running low on memory. Close other applications and try again.")
    else:
        suggestions.append("An unexpected error occurred. Check the Technical Details tab for more information.")
        suggestions.append("If the problem persists, try restarting the application.")
    suggestions.append("If these steps don't help, please report this issue with the full error report (click 'Copy Report').")
    return suggestions


class BuildSummaryDialog(QDialog):
    """Post-build summary with verification results."""

    def __init__(self, parent=None, appimage_path="",
                 file_size=0, is_elf=False, is_executable=False,
                 validation_warnings=None):
        super().__init__(parent)
        self.setWindowTitle("Build Summary")
        self.setMinimumSize(520, 400)
        self._appimage_path = appimage_path
        self._file_size = file_size
        self._is_elf = is_elf
        self._is_executable = is_executable
        self._validation_warnings = validation_warnings or []
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Header
        header = QHBoxLayout()
        icon_label = QLabel()
        icon = get_icon("emblem-default", "dialog-ok-apply", "emblem-ok")
        if not icon.isNull():
            icon_label.setPixmap(icon.pixmap(48, 48))
        header.addWidget(icon_label)
        header.addWidget(QLabel("<h2>Build Complete</h2>"))
        header.addStretch()
        layout.addLayout(header)

        # Path
        layout.addWidget(QLabel(f"<b>Location:</b> {self._appimage_path}"))

        # Details panel
        details_group = QWidget()
        details_group.setStyleSheet(
            "QWidget { background: palette(base); border: 1px solid palette(mid); "
            "border-radius: 6px; padding: 8px; }"
        )
        details_layout = QVBoxLayout(details_group)
        details_layout.setSpacing(4)
        details_layout.addWidget(QLabel(f"<b>Size:</b> {self._format_size(self._file_size)}"))
        details_layout.addWidget(QLabel(f"<b>Type:</b> {'ELF 64-bit executable' if self._is_elf else 'Unknown'}"))
        details_layout.addWidget(QLabel(f"<b>Executable:</b> {'Yes' if self._is_executable else 'No - run: chmod +x'}"))
        if not self._is_executable:
            details_layout.addWidget(QLabel(
                "<span style='color: #cc6600;'>⚠ The AppImage is not executable. "
                "Run <code>chmod +x</code> on it before use.</span>"
            ))
        layout.addWidget(details_group)

        # Warnings
        if self._validation_warnings:
            warn_label = QLabel("<b>Validation warnings:</b>")
            warn_label.setStyleSheet("color: #cc6600;")
            layout.addWidget(warn_label)
            for w in self._validation_warnings:
                wl = QLabel(f"  ⚠ {w}")
                wl.setWordWrap(True)
                wl.setStyleSheet("color: #cc6600;")
                layout.addWidget(wl)

        # Success message
        success_label = QLabel(
            "<span style='color: #4a9e4a; font-size: 11pt;'>"
            "✓ Your AppImage was built successfully and is ready to use!</span>"
        )
        layout.addWidget(success_label)

        # Usage tips
        tips_group = QWidget()
        tips_group.setStyleSheet(
            "QWidget { background: palette(window); border: 1px solid palette(mid); "
            "border-radius: 6px; padding: 8px; margin-top: 4px; }"
        )
        tips_layout = QVBoxLayout(tips_group)
        tips_layout.setSpacing(4)
        tips_layout.addWidget(QLabel("<b>💡 Quick tips:</b>"))
        tips_layout.addWidget(QLabel(
            "  • Run it: <code>./" + os.path.basename(self._appimage_path) + "</code>"
        ))
        tips_layout.addWidget(QLabel(
            "  • Make it executable once: <code>chmod +x " + os.path.basename(self._appimage_path) + "</code>"
        ))
        tips_layout.addWidget(QLabel(
            "  • Move it anywhere — AppImages are fully portable."
        ))
        if self._is_elf:
            tips_layout.addWidget(QLabel(
                "  ✓ Valid ELF binary — the AppImage was packaged correctly."
            ))
        layout.addWidget(tips_group)

        layout.addStretch()

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QPushButton(get_icon("dialog-close"), "Done")
        close_btn.setStyleSheet("QPushButton { padding: 6px 20px; }")
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

    @staticmethod
    def _format_size(bytes_val: int) -> str:
        if bytes_val < 1024:
            return f"{bytes_val} B"
        elif bytes_val < 1024 * 1024:
            return f"{bytes_val / 1024:.1f} KB"
        elif bytes_val < 1024 * 1024 * 1024:
            return f"{bytes_val / (1024 * 1024):.1f} MB"
        else:
            return f"{bytes_val / (1024 * 1024 * 1024):.2f} GB"
