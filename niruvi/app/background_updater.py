import logging
import threading
from dataclasses import dataclass

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from niruvi.app.self_update import compare_versions
from niruvi.app.update_sources import resolve_update_source
from niruvi.desktop.installation_registry import InstallationRegistry

UPDATE_INTERVAL_SETTING = "update_check_interval"
INTERVAL_OPTIONS = {
    "never": 0,
    "daily": 86400,
    "weekly": 604800,
    "monthly": 2592000,
}


@dataclass
class UpdateResult:
    app_name: str
    current_version: str
    latest_version: str
    download_url: str
    changelog: str | None = None
    source_type: str = "direct"
    sha256: str | None = None


class BackgroundUpdater(QObject):
    update_found = pyqtSignal(object)
    update_checked = pyqtSignal(str, bool)
    all_checked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_timer)
        self._running = False
        self._check_in_progress = False
        self._interval_seconds = 86400
        self._enabled = True
        self._auto_update = False

    def start(self, interval_seconds: int = 86400, auto_update: bool = False):
        self._enabled = True
        self._auto_update = auto_update
        self._interval_seconds = interval_seconds
        if interval_seconds > 0:
            ms = min(interval_seconds * 1000, 2147483647)
            self._timer.start(ms)
            self._running = True

    def stop(self):
        self._timer.stop()
        self._running = False

    def set_interval(self, interval_seconds: int):
        self._interval_seconds = interval_seconds
        if self._running:
            ms = min(interval_seconds * 1000, 2147483647)
            self._timer.setInterval(ms)

    def is_running(self) -> bool:
        return self._running

    def _on_timer(self):
        if not self._check_in_progress:
            self.check_all()

    def check_all(self):
        if self._check_in_progress:
            return
        self._check_in_progress = True
        # Network checks run off the GUI thread so the UI never blocks.
        thread = threading.Thread(target=self._check_all_worker, daemon=True)
        thread.start()

    def _check_all_worker(self):
        try:
            registry = InstallationRegistry()
            records = registry.get_all()
            apps_with_url = [r for r in records if r.update_url and r.auto_update]
            for record in apps_with_url:
                if not self._enabled:
                    break
                channel = record.update_channel or "stable"
                self._check_app(record.name, record.update_url, record.version, channel=channel)
        finally:
            self._check_in_progress = False
            self.all_checked.emit()

    def _check_app(self, app_name: str, update_url: str, current_version: str, channel: str = "stable"):
        """Check for updates for a single app.

        Args:
            app_name: Name of the app.
            update_url: Update URL to check.
            current_version: Currently installed version.
            channel: Update channel (stable, beta, nightly) — default "stable"
                     unless overridden by the record.
        """
        try:
            info = resolve_update_source(update_url, current_version, channel=channel)
            if not info or not info.version:
                self.update_checked.emit(app_name, False)
                return
            if compare_versions(info.version, "gt", current_version):
                result = UpdateResult(
                    app_name=app_name,
                    current_version=current_version,
                    latest_version=info.version,
                    download_url=info.download_url,
                    changelog=info.changelog,
                    source_type=info.source_type,
                    sha256=getattr(info, "sha256", None),
                )
                self.update_found.emit(result)
            else:
                self.update_checked.emit(app_name, True)
        except Exception as e:
            logging.debug("Background update check failed for %s: %s", app_name, e)
            self.update_checked.emit(app_name, False)

    def check_app_sync(
        self, app_name: str, update_url: str, current_version: str, channel: str = "stable"
    ) -> UpdateResult | None:
        try:
            info = resolve_update_source(update_url, current_version, channel=channel)
            if not info or not info.version:
                return None
            if compare_versions(info.version, "gt", current_version):
                return UpdateResult(
                    app_name=app_name,
                    current_version=current_version,
                    latest_version=info.version,
                    download_url=info.download_url,
                    changelog=info.changelog,
                    source_type=info.source_type,
                    sha256=getattr(info, "sha256", None),
                )
        except Exception as e:
            logging.debug("Sync update check failed for %s: %s", app_name, e)
        return None
