"""Repair Engine — detect and fix broken installations.

Can restore missing desktop entries, icons, AppRun executability,
manifest files, registry entries, and permissions.
"""

import logging
import os
import shutil
import stat
import subprocess
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

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
        return f"Repair: {self.success_count} succeeded, {self.failure_count} failed out of {len(self.actions)} actions"


def _refresh_desktop_db():
    for cmd in ("update-desktop-database", "gtk-update-icon-cache"):
        try:
            subprocess.run(
                [cmd, os.path.expanduser("~/.local/share/applications")],
                capture_output=True,
                timeout=30,
            )
        except Exception as e:
            logger.debug("Failed to run %s: %s", cmd, e, exc_info=True)
    for kde in ("kbuildsycoca6", "kbuildsycoca5"):
        try:
            subprocess.run([kde], capture_output=True, timeout=30)
        except Exception as e:
            logger.debug("Failed to run %s: %s", kde, e, exc_info=True)


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
            f'Exec="{os.path.join(app_dir, "AppRun")}" %F\n'
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


def repair_registry_entry(app_name: str, app_dir: str, version: str = "", update_url: str = "") -> RepairAction:
    def _do() -> bool:
        try:
            from niruvi.desktop.installation_registry import InstallationRecord, InstallationRegistry

            registry = InstallationRegistry()
            existing = registry.get(app_name)
            record = InstallationRecord(
                name=app_name,
                path=app_dir,
                version=version or (existing.version if existing else ""),
                update_url=update_url or (existing.update_url if existing else ""),
                desktop_file=os.path.expanduser(f"~/.local/share/applications/{app_name}.desktop"),
            )
            registry.add(record)
            return True
        except Exception as e:
            logger.warning("Failed to register %s in Niruvi: %s", app_name, e, exc_info=True)
            return False

    return RepairAction(f"Register {app_name} in Niruvi", _do)


def repair_manifest(app_dir: str) -> RepairAction:
    def _do() -> bool:
        try:
            from niruvi.core.manifest import MANIFEST_FILENAME, default_manifest

            install_dir = Path(app_dir) / ".niruvi-install"
            install_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = install_dir / MANIFEST_FILENAME
            if not manifest_path.is_file():
                name = os.path.basename(app_dir)
                m = default_manifest(app_id=name, app_name=name)
                m.to_file(str(manifest_path))
            return True
        except Exception as e:
            logger.warning("Failed to generate manifest for %s: %s", app_dir, e, exc_info=True)
            return False

    return RepairAction(f"Generate manifest for {os.path.basename(app_dir)}", _do)


def repair_junest_relocation(app_name: str, app_dir: str) -> RepairAction:
    """Move a JuNest container app out of a path containing spaces.

    JuNest's bundled scripts break on unquoted paths; moving the install
    to a space-free directory fixes launch errors like
    'bwrap: execvp: No such file or directory'.  Returns the new path via
    ``action.new_path`` once executed.
    """

    def _do() -> bool:
        if _action.ran:
            return True
        _action.ran = True
        try:
            from niruvi.desktop.installation_registry import (
                InstallationRecord,
                InstallationRegistry,
            )
            from niruvi.installer.junest import (
                is_junest_app,
                path_has_spaces,
                suggest_space_free_path,
            )

            if not is_junest_app(app_dir):
                raise RepairError(f"{app_dir} does not bundle a JuNest container")
            if not path_has_spaces(app_dir):
                raise RepairError(f"{app_dir} has no spaces in its path")
            target = suggest_space_free_path(app_dir)
            if os.path.exists(target):
                raise RepairError(f"Target already exists: {target}")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.move(app_dir, target)

            registry = InstallationRegistry()
            record = registry.get(app_name)
            if record:
                new_record = InstallationRecord(
                    name=record.name,
                    path=target,
                    version=record.version,
                    install_date=record.install_date,
                    install_type=record.install_type,
                    source_sha256=record.source_sha256,
                    desktop_file="",
                    desktop_shortcut="",
                    update_url=record.update_url,
                    architecture=record.architecture,
                    display_name_override=record.display_name_override,
                    custom_icon_path=record.custom_icon_path,
                    env_vars=record.env_vars,
                    run_args=record.run_args,
                    auto_update=record.auto_update,
                    update_channel=record.update_channel,
                    sandbox_config=record.sandbox_config,
                    size=record.size,
                    tags=record.tags,
                )
                registry.remove(app_name)
                registry.add(new_record)
                registry.flush()

            old_desktop = record.desktop_file if record else ""
            if old_desktop and os.path.isfile(old_desktop):
                try:
                    os.unlink(old_desktop)
                except OSError:
                    logger.debug("Could not remove old desktop entry %s", old_desktop, exc_info=True)
            try:
                from niruvi.desktop.desktop_utils import create_desktop_entry

                create_desktop_entry(target, app_name)
            except Exception as e:
                logger.debug("Could not recreate desktop entry: %s", e, exc_info=True)
            _refresh_desktop_db()
            _action.new_path = target
            return True
        except Exception as e:
            logger.warning("JuNest relocation failed for %s: %s", app_dir, e, exc_info=True)
            raise

    _action = RepairAction(f"Move JuNest container out of space-containing path: {app_dir}", _do)
    _action.new_path = ""
    _action.ran = False
    return _action


def repair_full(app_name: str, app_dir: str) -> RepairReport:
    """Run all repair actions and return a report."""
    report = RepairReport()
    executed: set[int] = set()
    target_dir = app_dir
    if _needs_junest_relocation(app_dir):
        relocation = repair_junest_relocation(app_name, app_dir)
        report.add(relocation)
        relocation.execute()
        executed.add(id(relocation))
        target_dir = relocation.new_path or app_dir
    report.add(repair_apprun(target_dir))
    report.add(repair_desktop_entry(app_name, target_dir))
    report.add(repair_icon(app_name, target_dir))
    report.add(repair_registry_entry(app_name, target_dir))
    report.add(repair_manifest(target_dir))
    for action in report.actions:
        if id(action) in executed:
            continue
        action.execute()
    _refresh_desktop_db()
    return report


def _needs_junest_relocation(app_dir: str) -> bool:
    try:
        from niruvi.installer.junest import is_junest_app, path_has_spaces

        return bool(app_dir) and path_has_spaces(app_dir) and is_junest_app(app_dir)
    except Exception as e:
        logger.debug("JuNest relocation pre-check failed: %s", e, exc_info=True)
        return False
