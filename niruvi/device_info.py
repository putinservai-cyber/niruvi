import os
import platform
import subprocess

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QSplitter,
    QTextBrowser, QDialogButtonBox,
)

from niruvi.utils import get_icon


def _collect_system_info() -> dict[str, str]:
    info = {}

    info["Operating System"] = f"{platform.system()} {platform.release()}"
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
    info["Distribution"] = distro
    info["Kernel"] = platform.version()
    info["Architecture"] = platform.machine()
    info["Hostname"] = platform.node()

    info["Processor"] = platform.processor()
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    info["CPU"] = line.split(":", 1)[1].strip()
                    break
    except Exception:
        info["CPU"] = "Unknown"
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

    return info


class DeviceInfoDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Device Information")
        self.setMinimumSize(640, 480)
        self._info = _collect_system_info()
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        nav = QListWidget()
        nav.setFixedWidth(180)
        nav.setCurrentRow(0)
        nav.setStyleSheet(
            "QListWidget { border: none; }"
            "QListWidget::item { padding: 8px 12px; }"
            "QListWidget::item:selected { background: palette(highlight); color: palette(highlighted-text); }"
        )

        categories = list(self._info.keys())
        icon_map = {
            "Operating System": "computer",
            "Distribution": "computer",
            "Kernel": "computer",
            "Architecture": "computer",
            "Hostname": "computer",
            "Processor": "cpu",
            "CPU": "cpu",
            "CPU Cores": "cpu",
            "Memory": "drive-harddisk",
            "GPU (OpenGL)": "video-display",
            "GPU (Vulkan)": "video-display",
        }
        for key in categories:
            icon_name = icon_map.get(key, "system-search")
            nav.addItem(QListWidgetItem(get_icon(icon_name, "system-search"), key))
        nav.currentRowChanged.connect(self._on_page_changed)
        splitter.addWidget(nav)

        self.content = QTextBrowser()
        self.content.setOpenExternalLinks(True)
        self.content.setFont(QFont("sans-serif", 10))
        self.content.setStyleSheet("QTextBrowser { padding: 12px; }")
        splitter.addWidget(self.content)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        layout.addWidget(splitter, 1)

        btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn.rejected.connect(self.accept)
        layout.addWidget(btn)

        self._show_page(0)

    def _on_page_changed(self, idx: int):
        self._show_page(idx)

    def _show_page(self, idx: int):
        keys = list(self._info.keys())
        if 0 <= idx < len(keys):
            key = keys[idx]
            val = self._info[key]
            html = f"<h2>{key}</h2><pre style='font-size:10pt;'>{val}</pre>"
            self.content.setHtml(html)
