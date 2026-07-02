"""Repair Engine — detect and fix broken installations.

Can restore missing desktop entries, icons, AppRun executability,
manifest files, registry entries, and permissions.
"""

import logging
import os
import shutil
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


class RepairError(Exception):
    """Raised when a repair operation fails."""


class RepairAction:
    """A single repair action with status tracking."""

    def __init__(self, description: str, repair_fn: Callable[[], bool]):
        self.description = description
        self._repair_fn = repair_fn
        self.success = False
        self.error: str | None = None

    def execute(self) -> bool:
        try:
            self.success = self._repair_fn()
            return self.success
        except Exception as e:
            self.success = False
            self.error = str(e)
            return False

    def __repr__(self):
        status = "OK" if self.success else f"FAIL: {self.error}" if self.error else "PENDING"
        return f"[{status}] {self.description}"


class RepairReport:
    """Summary of all repair actions performed."""

    def __init__(self):
        self.actions: list[RepairAction] = []
        self.timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def add(self, action: RepairAction):
        self.actions.append(action)

    @property
    def success_count(self) -> int:
        return sum(1 for a in self.actions if a.success)

    @property
    def failure_count(self) -> int:
        return sum(1 for a in self.actions if not a.success)

    @property
    def all_succeeded(self) -> bool:
        return self.failure_count == 0

    def summary(self) -> str:
        return (f"Repair: {self.success_count} succeeded, "
                f"{self.failure_count} failed out of {len(self.actions)} actions")


def _refresh_desktop_db():
    for cmd in ("update-desktop-database", "gtk-update-icon-cache"):
        try:
            subprocess.run(
                [cmd, os.path.expanduser("~/.local/share/applications")],
                capture_output=True, timeout=30,
            )
        except Exception:
            pass
    for kde in ("kbuildsycoca6", "kbuildsycoca5"):
        try:
            subprocess.run([kde], capture_output=True, timeout=30)
        except Exception:
            pass


def repair_apprun(app_dir: str) -> RepairAction:
    def _do() -> bool:
        apprun = os.path.join(app_dir, "AppRun")
        if not os.path.isfile(apprun):
            return False
        mode = os.stat(apprun).st_mode
        if not (mode & stat.S_IXUSR):
            os.chmod(apprun, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return True
    return RepairAction(f"Fix AppRun permissions in {app_dir}", _do)


def repair_desktop_entry(app_name: str, app_dir: str, icon_path: str = "") -> RepairAction:
    def _do() -> bool:
        desktop_dir = os.path.expanduser("~/.local/share/applications")
        os.makedirs(desktop_dir, exist_ok=True)
        icon = icon_path or os.path.join(app_dir, ".DirIcon")
        if not os.path.isfile(icon):
            icon = "system-software-install"
        content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={app_name}\n"
            f"Exec={os.path.join(app_dir, 'AppRun')} %F\n"
            f"Icon={icon}\n"
            "Terminal=false\n"
            "Categories=Utility;\n"
            "StartupNotify=true\n"
        )
        dest = os.path.join(desktop_dir, f"{app_name}.desktop")
        with open(dest, "w") as f:
            f.write(content)
        return True
    return RepairAction(f"Create desktop entry for {app_name}", _do)


def repair_icon(app_name: str, app_dir: str) -> RepairAction:
    def _do() -> bool:
        icons_dir = os.path.expanduser("~/.local/share/icons/hicolor/256x256/apps")
        os.makedirs(icons_dir, exist_ok=True)
        for ext in (".png", ".svg", ".xpm"):
            candidates = list(Path(app_dir).rglob(f"*{ext}"))
            if candidates:
                dest = os.path.join(icons_dir, f"{app_name}{ext}")
                shutil.copy2(str(candidates[0]), dest)
                return True
        return False
    return RepairAction(f"Install icon for {app_name}", _do)


def repair_registry_entry(app_name: str, app_dir: str, version: str = "",
                           update_url: str = "") -> RepairAction:
    def _do() -> bool:
        try:
            from niruvi.desktop.installation_registry import InstallationRegistry, InstallationRecord
            registry = InstallationRegistry()
            existing = registry.get(app_name)
            record = InstallationRecord(
                name=app_name,
                path=app_dir,
                version=version or (existing.version if existing else ""),
                update_url=update_url or (existing.update_url if existing else ""),
                desktop_file=os.path.expanduser(
                    f"~/.local/share/applications/{app_name}.desktop"
                ),
            )
            registry.add(record)
            return True
        except Exception:
            return False
    return RepairAction(f"Register {app_name} in Niruvi", _do)


def repair_manifest(app_dir: str) -> RepairAction:
    def _do() -> bool:
        try:
            from niruvi.core.manifest import default_manifest, MANIFEST_FILENAME
            install_dir = Path(app_dir) / ".niruvi-install"
            install_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = install_dir / MANIFEST_FILENAME
            if not manifest_path.is_file():
                name = os.path.basename(app_dir)
                m = default_manifest(app_id=name, app_name=name)
                m.to_file(str(manifest_path))
            return True
        except Exception:
            return False
    return RepairAction(f"Generate manifest for {os.path.basename(app_dir)}", _do)


def repair_full(app_name: str, app_dir: str) -> RepairReport:
    """Run all repair actions and return a report."""
    report = RepairReport()
    report.add(repair_apprun(app_dir))
    report.add(repair_desktop_entry(app_name, app_dir))
    report.add(repair_icon(app_name, app_dir))
    report.add(repair_registry_entry(app_name, app_dir))
    report.add(repair_manifest(app_dir))
    for action in report.actions:
        action.execute()
    _refresh_desktop_db()
    return report
