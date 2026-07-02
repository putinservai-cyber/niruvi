import os
import platform
import subprocess

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QScrollArea,
    QWidget, QDialogButtonBox, QLabel, QFrame,
)

from niruvi.app.health_check import check_system_compatibility
from niruvi.utils.sound_manager import play as play_sound
from niruvi.utils import get_icon


_ICON_MAP = {
    "Operating System": "globe",
    "Kernel": "code",
    "Architecture": "cpu",
    "Hostname": "identification-card",
    "Processor": "cpu",
    "CPU": "cpu",
    "CPU Cores": "cpu",
    "Memory": "hard-drive",
    "GPU (OpenGL)": "video-display",
    "GPU (Vulkan)": "video-display",
    "Mesa": "preferences-system",
    "glibc": "code",
}


def _collect_system_info() -> dict[str, str]:
    info = {}

    distro = "unknown"
    try:
        if os.path.exists("/etc/os-release"):
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        distro = line.split("=", 1)[1].strip().strip('"')
                        break
    except Exception:
        pass
    info["Operating System"] = distro
    info["Kernel"] = platform.release()
    info["Architecture"] = platform.machine()
    info["Hostname"] = platform.node()

    cpu_model = "Unknown"
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    cpu_model = line.split(":", 1)[1].strip()
                    break
    except Exception:
        pass
    proc = platform.processor()
    if not proc:
        proc = cpu_model if cpu_model != "Unknown" else platform.machine()
    info["Processor"] = proc
    info["CPU"] = cpu_model
    cpu_count = os.cpu_count()
    info["CPU Cores"] = str(cpu_count) if cpu_count else "Unknown"

    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    gb = kb / 1024 / 1024
                    info["Memory"] = f"{gb:.1f} GiB"
                    break
    except Exception:
        info["Memory"] = "Unknown"

    gpu_lines = []
    try:
        result = subprocess.run(
            ["glxinfo", "-B"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                low = line.lower()
                if "opengl renderer" in low or "opengl vendor" in low or "opengl version" in low:
                    gpu_lines.append(line.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    info["GPU (OpenGL)"] = "\n".join(gpu_lines) if gpu_lines else "Not available"

    vulkan_lines = []
    try:
        result = subprocess.run(
            ["vulkaninfo", "--summary"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                low = line.strip().lower()
                if low.startswith("gpu") or "device name" in low or "driver version" in low:
                    vulkan_lines.append(line.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    info["GPU (Vulkan)"] = "\n".join(vulkan_lines[:6]) if vulkan_lines else "Not available"

    # Mesa version — try rpm, then dpkg, then parse ldconfig
    try:
        r = subprocess.run(["rpm", "-q", "mesa-dri-drivers", "--qf", "%{VERSION}"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            info["Mesa"] = r.stdout.strip()
    except Exception:
        pass
    if "Mesa" not in info:
        try:
            r = subprocess.run(["dpkg-query", "-W", "-f", "${Version}", "libgl1-mesa-dri"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                info["Mesa"] = r.stdout.strip()
        except Exception:
            pass
    if "Mesa" not in info:
        try:
            r = subprocess.run(["ldconfig", "-p"], capture_output=True, text=True, timeout=5)
            for line in r.stdout.splitlines():
                if "libGL.so" in line:
                    # extract version from symlink target like libGL.so.1.7.0
                    parts = line.strip().split("=>")
                    if len(parts) == 2:
                        lib = parts[1].strip()
                        info["Mesa"] = os.path.basename(lib).replace("libGL.so.", "")
                    break
        except Exception:
            pass
    # glibc version — try rpm, then dpkg, then ldd --version
    try:
        r = subprocess.run(["rpm", "-q", "glibc", "--qf", "%{VERSION}"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            info["glibc"] = r.stdout.strip()
    except Exception:
        pass
    if "glibc" not in info:
        try:
            r = subprocess.run(["dpkg-query", "-W", "-f", "${Version}", "libc6"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                info["glibc"] = r.stdout.strip()
        except Exception:
            pass
    if "glibc" not in info:
        try:
            r = subprocess.run(["/usr/bin/ldd", "--version"], capture_output=True, text=True, timeout=5)
            for line in r.stdout.splitlines():
                if "glibc" in line.lower() or "libc" in line.lower():
                    # e.g. "ldd (GNU libc) 2.35"
                    parts = line.rsplit(None, 1)
                    if len(parts) == 2:
                        info["glibc"] = parts[1]
                    break
        except Exception:
            pass

    return info


def _build_info_row(icon_name: str, key: str, value: str) -> QWidget:
    row = QWidget()
    row.setStyleSheet("""
        QWidget:hover {
            background-color: rgba(128, 128, 128, 0.08);
            border-radius: 4px;
        }
    """)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(12, 6, 12, 6)
    layout.setSpacing(10)

    icon_lbl = QLabel()
    icon = get_icon(icon_name)
    if icon and not icon.isNull():
        pixmap = icon.pixmap(20, 20)
        icon_lbl.setPixmap(pixmap)
    icon_lbl.setFixedSize(24, 24)
    icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(icon_lbl)

    key_lbl = QLabel(key)
    key_lbl.setFont(QFont("sans-serif", 10, QFont.Weight.Bold))
    key_lbl.setFixedWidth(160)
    layout.addWidget(key_lbl)

    escaped = str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
    val_lbl = QLabel(escaped)
    val_lbl.setWordWrap(True)
    val_lbl.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse
    )
    layout.addWidget(val_lbl, 1)

    return row


class DeviceInfoDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Device Information")
        self.setMinimumSize(580, 450)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._info = _collect_system_info()
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QWidget()
        header.setStyleSheet("background-color: palette(window); border-bottom: 1px solid palette(mid);")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 14, 16, 14)
        header_layout.setSpacing(12)

        header_icon = QLabel()
        icon = get_icon("computer", "device-laptop", "monitor")
        if icon and not icon.isNull():
            pixmap = icon.pixmap(40, 40)
            header_icon.setPixmap(pixmap)
        header_icon.setFixedSize(48, 48)
        header_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(header_icon)

        header_text = QLabel("<h2>Device</h2>")
        header_layout.addWidget(header_text, 1)
        layout.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setSpacing(0)
        content_layout.setContentsMargins(0, 4, 0, 4)

        for key, val in self._info.items():
            icon_name = _ICON_MAP.get(key, "dialog-information")
            row = _build_info_row(icon_name, key, val)
            content_layout.addWidget(row)

            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setFrameShadow(QFrame.Shadow.Sunken)
            sep.setStyleSheet("color: palette(midlight);")
            content_layout.addWidget(sep)

        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        compat = check_system_compatibility()
        if not compat["healthy"] or compat.get("has_warnings"):
            note = QLabel(
                "<p style='color:#cc7700;font-size:0.9em;'>"
                "Some apps may be incompatible with your system "
                "(new kernel, glibc, or graphics drivers). "
                "If an app fails to run, check the launch error dialog "
                "for suggestions.</p>"
            )
            note.setWordWrap(True)
            note.setContentsMargins(16, 8, 16, 8)
            layout.addWidget(note)

        btn_bar = QWidget()
        btn_layout = QHBoxLayout(btn_bar)
        btn_layout.setContentsMargins(12, 8, 12, 8)
        btn_layout.addStretch()
        close_btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_btn.rejected.connect(lambda: (play_sound("click"), self.accept()))
        btn_layout.addWidget(close_btn)
        layout.addWidget(btn_bar)
