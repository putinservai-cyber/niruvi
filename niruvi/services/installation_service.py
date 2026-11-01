"""Service for managing installed applications.

Handles the business logic for installing, uninstalling, and tracking
AppImages, decoupled from the UI layer.
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from niruvi.core.scanner import scan_directory
from niruvi.core.verification import verify_sha256
from niruvi.desktop.installation_registry import InstallationRecord
from niruvi.repositories.installation_record import InstallationRecordRepository
from niruvi.utils.sound_manager import play as play_sound

logger = logging.getLogger(__name__)


class InstallationService:
    """Service for managing installed applications.

    Handles the business logic for installing, uninstalling, and tracking
    AppImages, decoupled from the UI layer.

    Uses dependency injection for the repository to enable testing.
    """

    def __init__(self, repository: InstallationRecordRepository | None = None):
        self._repository = repository or InstallationRecordRepository()
        self._settings: Any = None  # Will be set via setter or property

    @property
    def settings(self):
        """Get the application settings."""
        if self._settings is None:
            from niruvi.config import get_settings

            self._settings = get_settings()
        return self._settings

    @settings.setter
    def settings(self, value):
        self._settings = value

    def install_record(self, record: InstallationRecord) -> bool:
        """Install a new application record.

        Args:
            record: The InstallationRecord to install

        Returns:
            True if installation was successful, False otherwise
        """
        try:
            # Validate the record
            if not record.name:
                logger.error("Cannot install record without a name")
                play_sound("error")
                return False

            # Check if already installed
            if self._repository.contains(record.name):
                logger.warning("Application '%s' is already installed", record.name)
                play_sound("warning")
                return False

            # Add the record
            self._repository.add(record)
            logger.info("Application '%s' installed successfully", record.name)
            play_sound("success")
            return True

        except Exception as e:
            logger.error("Failed to install application: %s", e, exc_info=True)
            play_sound("error")
            return False

    def uninstall_record(self, name: str) -> bool:
        """Uninstall an application record.

        Args:
            name: The name of the application to uninstall

        Returns:
            True if uninstallation was successful, False otherwise
        """
        try:
            # Get the record before removing
            record = self._repository.get(name)
            if record is None:
                logger.warning("Application '%s' is not installed", name)
                play_sound("warning")
                return False

            # Remove the record from repository
            self._repository.remove(name)

            # Remove associated files
            if record.path and os.path.exists(record.path):
                try:
                    shutil.rmtree(record.path)
                    logger.debug("Removed application directory: %s", record.path)
                except OSError as e:
                    logger.warning("Failed to remove application directory: %s", e)

            # Remove desktop file
            desktop_file = record.desktop_file
            if desktop_file and os.path.exists(desktop_file):
                try:
                    os.remove(desktop_file)
                    logger.debug("Removed desktop file: %s", desktop_file)
                except OSError as e:
                    logger.warning("Failed to remove desktop file: %s", e)

            logger.info("Application '%s' uninstalled successfully", name)
            play_sound("success")
            return True

        except Exception as e:
            logger.error("Failed to uninstall application: %s", e, exc_info=True)
            play_sound("error")
            return False

    def scan_installed(self, progress_callback: Callable | None = None) -> list[dict[str, Any]]:
        """Scan all installed applications for security issues.

        Args:
            progress_callback: Optional callback(progress_percent) for UI updates

        Returns:
            List of scan results for all installed applications
        """
        results: list[dict[str, Any]] = []
        all_records = self._repository.get_all()

        total = len(all_records)
        if total == 0:
            return results

        for index, record in enumerate(all_records):
            try:
                # Scan the application directory
                if record.path and os.path.exists(record.path):
                    file_findings = scan_directory(record.path)
                    results.append(
                        {
                            "path": record.path,
                            "verdict": "suspicious" if file_findings else "clean",
                            "matches": file_findings,
                            "record": record,
                        }
                    )

                    # Call progress callback if provided
                    if progress_callback and index < total - 1:
                        progress_pct = int((index + 1) / total * 100)
                        progress_callback(progress_pct)
                else:
                    # Record path doesn't exist, add minimal result
                    results.append({"path": record.path or "", "verdict": "clean", "matches": [], "record": record})

            except Exception as e:
                logger.error("Failed to scan application '%s': %s", record.name, e, exc_info=True)
                # Add error result
                results.append(
                    {
                        "path": record.path or "",
                        "verdict": "error",
                        "matches": [f"scan_failed: {str(e)[:100]}"],
                        "record": record,
                    }
                )

        return results

    def verify_installation(self, name: str) -> tuple[bool, str]:
        """Verify the integrity of an installed application.

        Args:
            name: The name of the application to verify

        Returns:
            Tuple of (is_valid, message)
        """
        try:
            record = self._repository.get(name)
            if record is None:
                return False, f"Application '{name}' is not installed"

            # Check if the path still exists
            if not record.path or not os.path.exists(record.path):
                return False, f"Application '{name}' path does not exist: {record.path}"

            # Verify SHA256 if available
            if record.source_sha256:
                verification = verify_sha256(record.path, record.source_sha256)
                if not verification.passed:
                    detail = "; ".join(verification.errors[:2]) or verification.details
                    return False, f"SHA256 mismatch for '{name}': {detail[:100]}"

            return True, f"Application '{name}' verification passed"

        except Exception as e:
            logger.error("Failed to verify installation '%s': %s", name, e, exc_info=True)
            return False, str(e)[:200]

    def get_installation_path(self, name: str) -> Path | None:
        """Get the install path for an application.

        Args:
            name: The application name

        Returns:
            Path object or None if not found
        """
        record = self._repository.get(name)
        if record and record.path:
            return Path(record.path)
        return None

    def get_display_name(self, name: str) -> str:
        """Get the display name for an application, with fallback.

        Args:
            name: The registry name

        Returns:
            Display name, preferring custom override if available
        """
        record = self._repository.get(name)
        if record and record.display_name_override:
            return record.display_name_override
        return name
