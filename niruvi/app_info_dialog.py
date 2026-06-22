import datetime
import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QPixmap
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QFrame, QSizePolicy, QDialogButtonBox,
)

from niruvi.utils import get_icon


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / 1024 ** 2:.1f} MB"
    else:
        return f"{size_bytes / 1024 ** 3:.2f} GB"


def _format_date(timestamp: float) -> str:
    dt = datetime.datetime.fromtimestamp(timestamp)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


class AppInfoDialog(QDialog):
    def __init__(self, app_name: str, app_info: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{app_name} — App Info")
        self.setMinimumWidth(440)
        self.setModal(True)
        self._app_name = app_name
        self._info = app_info
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(12)

        icon_path = self._info.get("icon_path")
        icon_label = QLabel()
        if icon_path and os.path.exists(icon_path):
            pixmap = QPixmap(icon_path)
            if not pixmap.isNull():
                icon_label.setPixmap(pixmap.scaled(
                    48, 48, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
        else:
            icon_label.setPixmap(
                get_icon("package-x-generic", "application-x-archive").pixmap(48, 48)
            )
        icon_label.setFixedSize(48, 48)
        header.addWidget(icon_label)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)

        display_name = self._info.get("display_name", self._app_name)
        title = QLabel(f"<b>{display_name}</b>")
        font = title.font()
        font.setPointSize(16)
        title.setFont(font)
        title_col.addWidget(title)

        version_str = self._info.get("version", "unknown")
        ver = QLabel(f"Version: {version_str}")
        ver.setStyleSheet("opacity: 0.7;")
        ver.setEnabled(False)
        title_col.addWidget(ver)

        header.addLayout(title_col, 1)
        layout.addLayout(header)

        layout.addWidget(QFrame(frameShape=QFrame.Shape.HLine, frameShadow=QFrame.Shadow.Sunken))

        fields = []
        app_dir = self._info.get("path", "?")
        fields.append(("App Name", self._app_name))

        if os.path.isdir(app_dir):
            total = 0
            for root, dirs, files in os.walk(app_dir):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
            fields.append(("Size", _format_size(total)))
            ctime = os.path.getctime(app_dir)
            fields.append(("Installed", _format_date(ctime)))
            mtime = os.path.getmtime(app_dir)
            fields.append(("Last Modified", _format_date(mtime)))

        fields.append(("Path", app_dir))

        desktop_file = self._info.get("desktop_file")
        if desktop_file:
            fields.append(("Desktop Entry", desktop_file))

        shortcut = self._info.get("desktop_shortcut")
        if shortcut:
            fields.append(("Shortcut", shortcut))

        for label, value in fields:
            row = QHBoxLayout()
            row.setSpacing(8)
            lbl = QLabel(f"<b>{label}:</b>")
            lbl.setFixedWidth(130)
            val = QLabel(str(value))
            val.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            val.setWordWrap(True)
            val.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            row.addWidget(lbl)
            row.addWidget(val, 1)
            layout.addLayout(row)

        layout.addStretch()

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(self.accept)
        layout.addWidget(btn_box)
