"""App Store dialog — discover and install apps from a remote catalog."""

import logging
import os

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from niruvi.core.worker import DownloadWorker, start_worker
from niruvi.utils import get_icon
from niruvi.utils.sound_manager import play as play_sound
from niruvi.utils.sound_manager import play_and
from niruvi.utils.theme_engine import style

logger = logging.getLogger(__name__)


class _CatalogFetchWorker(QThread):
    fetched = pyqtSignal(list, str)
    failed = pyqtSignal(str)

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self._url = url

    def run(self):
        from niruvi.app.store import fetch_index

        try:
            entries = fetch_index(self._url)
            self.fetched.emit(entries, self._url)
        except Exception as e:
            self.failed.emit(str(e))


class StoreDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("App Store")
        self.setMinimumSize(560, 480)
        self.resize(640, 540)
        self._entries: list[dict] = []
        self._entries_url = ""
        self._download_worker: DownloadWorker | None = None
        self._init_ui()
        self._refresh()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        header_row = QHBoxLayout()
        title = QLabel("<b>Discover AppImages</b>")
        header_row.addWidget(title)
        header_row.addStretch()
        self.refresh_btn = QPushButton(get_icon("view-refresh"), "Refresh")
        self.refresh_btn.clicked.connect(lambda: play_and("click", self._refresh))
        header_row.addWidget(self.refresh_btn)
        layout.addLayout(header_row)

        self.status_label = QLabel("Loading catalog...")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(style("color: {colors.subtle};"))
        layout.addWidget(self.status_label)

        filter_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search by name, description or category...")
        self.search_edit.textChanged.connect(lambda _: self._apply_filter())
        filter_row.addWidget(self.search_edit, 1)
        self.arch_combo = QComboBox()
        self.arch_combo.addItem("Any architecture", "")
        from niruvi.build.page import SUPPORTED_ARCHES, detect_host_arch

        for arch in SUPPORTED_ARCHES:
            self.arch_combo.addItem(arch, arch)
        idx = self.arch_combo.findData(detect_host_arch())
        if idx >= 0:
            self.arch_combo.setCurrentIndex(idx)
        self.arch_combo.currentIndexChanged.connect(lambda _: self._apply_filter())
        filter_row.addWidget(self.arch_combo)
        layout.addLayout(filter_row)

        self.app_list = QListWidget()
        self.app_list.setAlternatingRowColors(True)
        self.app_list.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.app_list, 1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.install_btn = QPushButton(get_icon("list-add"), "Install Selected")
        self.install_btn.setEnabled(False)
        self.install_btn.clicked.connect(self._install_selected)
        btn_row.addWidget(self.install_btn)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(lambda: play_and("navigation", self.reject))
        btn_row.addWidget(self.close_btn)
        layout.addLayout(btn_row)

    def _refresh(self):
        from niruvi.app.store import get_index_url, load_cache

        url = get_index_url()
        if not url:
            self.status_label.setText(
                "No catalog URL configured. Set one in Settings → App Store "
                '(a JSON index with apps: [{"name", "url", ...}]).'
            )
            return
        cached = load_cache()
        if cached:
            entries, cached_url, fetched_ts = cached
            self._set_entries(entries, cached_url)
            import time

            self.status_label.setText(
                f"Showing cached catalog from {time.strftime('%Y-%m-%d %H:%M', time.localtime(fetched_ts))}. "
                "Refreshing..."
            )
        else:
            self.status_label.setText("Loading catalog...")
        self.refresh_btn.setEnabled(False)
        self._fetch_worker = _CatalogFetchWorker(url, self)
        self._fetch_worker.fetched.connect(self._on_fetched)
        self._fetch_worker.failed.connect(self._on_fetch_failed)
        self._fetch_worker.finished.connect(self._on_fetch_done)
        self._fetch_worker.start()

    def _on_fetched(self, entries: list, url: str):
        self._set_entries(entries, url)
        self.status_label.setText(f"Catalog loaded ({len(entries)} apps).")

    def _on_fetch_failed(self, error: str):
        if self.app_list.count() == 0:
            self.status_label.setText(f"Could not load catalog: {error}")
        else:
            self.status_label.setText(f"Could not refresh catalog ({error}) — showing cached copy.")

    def _on_fetch_done(self):
        self.refresh_btn.setEnabled(True)

    def _set_entries(self, entries: list, url: str):
        self._entries = entries
        self._entries_url = url
        self._apply_filter()

    def _apply_filter(self):
        from niruvi.app.store import search

        arch = self.arch_combo.currentData()
        results = search(self._entries, self.search_edit.text(), arch)
        self.app_list.clear()
        for entry in results:
            name = entry.get("name", "?")
            version = entry.get("version", "")
            arch_s = entry.get("arch", "x86_64")
            desc = entry.get("description", "")
            text = name
            if version:
                text += f"  v{version}"
            if arch_s and arch_s != "any":
                text += f"  [{arch_s}]"
            item = QListWidgetItem(text)
            if desc:
                item.setToolTip(f"{name}\n\n{desc}\n\n{entry.get('url', '')}")
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.app_list.addItem(item)

    def _on_selection_changed(self):
        self.install_btn.setEnabled(bool(self.app_list.selectedItems()))

    def _install_selected(self):
        selected = self.app_list.selectedItems()
        if not selected:
            return
        entry = selected[0].data(Qt.ItemDataRole.UserRole)
        url = entry.get("url", "")
        if not url:
            play_sound("error")
            return
        play_sound("click")
        self.install_btn.setEnabled(False)
        self.close_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        import tempfile

        tmp_dir = tempfile.mkdtemp(prefix="niruvi-store-")
        dest = os.path.join(tmp_dir, f"{entry.get('name', 'app')}.AppImage")
        self._download_worker = DownloadWorker(url, dest, parent=self)
        self._download_worker.progress_updated.connect(self.progress_bar.setValue)
        self._download_worker.status_changed.connect(lambda msg: self.status_label.setText(msg))
        self._download_worker.finished.connect(lambda p: self._on_downloaded(p, tmp_dir))
        self._download_worker.error.connect(self._on_download_error)
        start_worker(self._download_worker)

    def _on_downloaded(self, path: str, tmp_dir: str):
        self.status_label.setText(f"Downloaded {os.path.basename(path)} — preparing install...")
        from niruvi.ui.wizard import InstallWizard

        wizard = InstallWizard(path, self)
        if wizard.exec() == QDialog.DialogCode.Accepted:
            self.accept()
        else:
            self.install_btn.setEnabled(True)
            self.close_btn.setEnabled(True)
            self.progress_bar.setVisible(False)
            try:
                os.remove(path)
            except OSError:
                pass

    def _on_download_error(self, error: str):
        play_sound("error")
        self.install_btn.setEnabled(True)
        self.close_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText(f"Download failed: {error}")
