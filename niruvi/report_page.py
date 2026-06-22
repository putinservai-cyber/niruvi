# Copyright (C) 2026 putinservai-cyber
# SPDX-License-Identifier: GPL-3.0-or-later

import webbrowser

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QHBoxLayout,
    QFrame,
)

from niruvi.constants import REPORT_ISSUES_URL
from niruvi.utils import get_icon


class ReportPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)

        title_layout = QHBoxLayout()
        icon_label = QLabel()
        icon_label.setPixmap(
            get_icon("bug", "tools-report-bug", "dialog-warning").pixmap(32, 32)
        )
        title_layout.addWidget(icon_label)

        title = QLabel("Report a Bug or Suggest a Feature")
        font = title.font()
        font.setPointSize(16)
        font.setBold(True)
        title.setFont(font)
        title_layout.addWidget(title)
        title_layout.addStretch()
        main_layout.addLayout(title_layout)

        subtitle = QLabel(
            "Found an issue or have an idea for a new feature? "
            "Please let us know by creating an issue on our GitHub page."
        )
        subtitle.setWordWrap(True)
        main_layout.addWidget(subtitle)

        main_layout.addWidget(
            QFrame(frameShape=QFrame.Shape.HLine, frameShadow=QFrame.Shadow.Sunken)
        )

        self.btn_github = QPushButton(
            get_icon("go-next", "arrow-right", "media-skip-forward"), "Open GitHub Issues Page"
        )
        self.btn_github.setMinimumHeight(40)
        main_layout.addWidget(self.btn_github)

        info_label = QLabel(
            "Clicking the button will open the new issue page in your web browser. "
            "Please provide as much detail as possible, including steps to reproduce "
            "the bug, your operating system, and any error messages you received."
        )
        info_label.setWordWrap(True)
        main_layout.addWidget(info_label)

        main_layout.addStretch()

        self.btn_github.clicked.connect(
            lambda: webbrowser.open(REPORT_ISSUES_URL)
        )
