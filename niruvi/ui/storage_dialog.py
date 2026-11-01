"""Storage Analyzer — shows where Niruvi uses disk space and reclaims leftovers.

The scan runs on a worker thread so the GUI never blocks (item 11), and the
numbers come from :mod:`niruvi.core.storage`, which separates app files, delta
seed AppImages, portable data, rollback copies and obsolete leftovers so that
nothing is counted twice (item 10).
"""

import logging
import os
import subprocess

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from niruvi.utils.sound_manager import play as play_sound
from niruvi.utils.styles import format_size
from niruvi.utils.theme_engine import style

logger = logging.getLogger(__name__)

_COLUMNS = ("Location", "App files", "Seed images", "Portable", "Rollback", "Leftovers", "Total")


class StorageScanWorker(QThread):
    done = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, apps, temp_root=None, parent=None):
        super().__init__(parent)
        self._apps = list(apps)
        self._temp_root = temp_root

    def run(self):
        try:
            from niruvi.core.storage import analyze_storage

            self.done.emit(analyze_storage(self._apps, self._temp_root))
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("Storage scan failed: %s", e, exc_info=True)
            self.error.emit(str(e))


class StorageDialog(QDialog):
    def __init__(self, apps, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Storage Analyzer")
        self.setMinimumSize(720, 460)
        self._apps = list(apps)
        self._worker: StorageScanWorker | None = None
        self._report = None

        root = QVBoxLayout(self)
        root.setSpacing(10)

        title = QLabel("Where Niruvi uses disk space")
        title.setStyleSheet(style("font-size: 15px; font-weight: bold; color: {colors.text};"))
        root.addWidget(title)

        self.summary = QLabel("Scanning…")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet(style("color: {colors.subtle};"))
        root.addWidget(self.summary)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, len(_COLUMNS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._scan)
        self.clean_temp_btn = QPushButton("Clean temporary files")
        self.clean_temp_btn.clicked.connect(self._clean_temp)
        self.clean_obsolete_btn = QPushButton("Remove leftover backups")
        self.clean_obsolete_btn.setToolTip(
            "Remove incomplete .bak/.backup/.staging folders left by interrupted "
            "operations. Rollback copies (.prev) and your apps are never touched."
        )
        self.clean_obsolete_btn.clicked.connect(self._clean_obsolete)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(self.clean_temp_btn)
        buttons.addWidget(self.clean_obsolete_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.refresh_btn)
        buttons.addWidget(close_btn)
        root.addLayout(buttons)

        self._scan()

    # ── scanning ─────────────────────────────────────────────────────────
    def _scan(self):
        if self._worker is not None and self._worker.isRunning():
            return
        self.refresh_btn.setEnabled(False)
        self.summary.setText("Scanning…")
        self._worker = StorageScanWorker(self._apps, parent=self)
        self._worker.done.connect(self._on_report)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(lambda: self.refresh_btn.setEnabled(True))
        self._worker.start()

    def _on_error(self, msg: str):
        self.summary.setText(f"Scan failed: {msg}")

    def _on_report(self, report):
        self._report = report
        rows = []
        for app in sorted(report.apps, key=lambda a: a.total, reverse=True):
            rows.append(
                (
                    app.name,
                    app.app_files,
                    app.seed_images,
                    app.portable_home + app.portable_config,
                    app.rollback,
                    app.obsolete,
                    app.total,
                )
            )
        for path, size in report.temp_dirs:
            rows.append((f"Temporary: {os.path.basename(path)}", 0, 0, 0, 0, size, size))

        self.table.setRowCount(len(rows))
        for r, values in enumerate(rows):
            for c, value in enumerate(values):
                if c == 0:
                    item = QTableWidgetItem(str(value))
                    item.setData(Qt.ItemDataRole.UserRole, values[0])
                else:
                    item = QTableWidgetItem(format_size(int(value)))
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(r, c, item)

        self.summary.setText(
            f"{len(report.apps)} application(s) · {format_size(report.apps_total)} · "
            f"temporary files {format_size(report.temp_total)} · "
            f"<b>total {format_size(report.grand_total)}</b>"
        )

    # ── cleanup ──────────────────────────────────────────────────────────
    def _clean_temp(self):
        from niruvi.core.storage import cleanup_stale_temp_dirs

        reply = QMessageBox.question(
            self,
            "Clean temporary files",
            "Delete Niruvi's temporary extraction folders?\n\n"
            "Only folders created by Niruvi under the temp directory are removed. "
            "Installed apps, portable data and rollback copies are kept.",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        active = list(getattr(self.parent(), "_temp_launch_dirs", []) or [])
        removed, freed = cleanup_stale_temp_dirs(max_age_seconds=0, active_paths=active)
        play_sound("success" if removed else "info")
        QMessageBox.information(
            self,
            "Cleanup complete",
            f"Removed {len(removed)} folder(s), {format_size(freed)} freed." if removed else "Nothing to clean up.",
        )
        self._scan()

    def _clean_obsolete(self):
        from niruvi.core.storage import cleanup_obsolete_backups

        reply = QMessageBox.question(
            self,
            "Remove leftover backups",
            "Remove incomplete backup folders (.bak/.backup/.staging) left by "
            "interrupted operations?\n\nRollback copies (.prev) and installed apps "
            "are not affected.",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        freed = 0
        for _name, path in self._apps:
            if path and os.path.isdir(path):
                freed += cleanup_obsolete_backups(_name, path)
        play_sound("success" if freed else "info")
        QMessageBox.information(
            self,
            "Cleanup complete",
            f"Freed {format_size(freed)}." if freed else "Nothing to remove.",
        )
        self._scan()

    def _open_path(self, path: str):
        try:
            subprocess.Popen(["xdg-open", path], start_new_session=True)
        except OSError as e:
            logger.debug("Could not open %s: %s", path, e)

    def closeEvent(self, event):
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(3000)
        super().closeEvent(event)
