import logging
from dataclasses import dataclass

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication

from niruvi.installation_registry import InstallationRegistry
from niruvi.update_sources import resolve_update_source
from niruvi.self_update import compare_versions

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
            self._timer.start(interval_seconds * 1000)
            self._running = True

    def stop(self):
        self._timer.stop()
        self._running = False

    def set_interval(self, interval_seconds: int):
        self._interval_seconds = interval_seconds
        if self._running:
            self._timer.setInterval(interval_seconds * 1000)

    def is_running(self) -> bool:
        return self._running

    def _on_timer(self):
        if not self._check_in_progress:
            self.check_all()

    def check_all(self):
        if self._check_in_progress:
            return
        self._check_in_progress = True
        try:
            registry = InstallationRegistry()
            records = registry.get_all()
            apps_with_url = [r for r in records if r.update_url and r.auto_update]
            for record in apps_with_url:
                self._check_app(record.name, record.update_url, record.version)
        finally:
            self._check_in_progress = False
            self.all_checked.emit()

    def _check_app(self, app_name: str, update_url: str, current_version: str):
        try:
            info = resolve_update_source(update_url, current_version, channel="stable")
            if not info or not info.version:
                self.update_checked.emit(app_name, False)
                return
            if compare_versions(info.version, 'gt', current_version):
                result = UpdateResult(
                    app_name=app_name,
                    current_version=current_version,
                    latest_version=info.version,
                    download_url=info.download_url,
                    changelog=info.changelog,
                    source_type=info.source_type,
                )
                self.update_found.emit(result)
            else:
                self.update_checked.emit(app_name, True)
        except Exception as e:
            logging.debug("Background update check failed for %s: %s", app_name, e)
            self.update_checked.emit(app_name, False)

    def check_app_sync(self, app_name: str, update_url: str,
                       current_version: str) -> UpdateResult | None:
        try:
            info = resolve_update_source(update_url, current_version, channel="stable")
            if not info or not info.version:
                return None
            if compare_versions(info.version, 'gt', current_version):
                return UpdateResult(
                    app_name=app_name,
                    current_version=current_version,
                    latest_version=info.version,
                    download_url=info.download_url,
                    changelog=info.changelog,
                    source_type=info.source_type,
                )
        except Exception as e:
            logging.debug("Sync update check failed for %s: %s", app_name, e)
        return None
