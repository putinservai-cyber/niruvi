"""Manager service that coordinates installation, scanning, and updates.

This service replaces the direct use of InstallationRegistry singleton
in the UI layer, enabling dependency injection and testability.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from niruvi.repositories.installation_record import InstallationRecordRepository
from niruvi.services.installation_service import InstallationService

logger = logging.getLogger(__name__)


class ManagerService:
    """Service that coordinates manager operations.

    Provides a clean interface for the UI layer to interact with
    installation management, scanning, and verification features.
    """

    def __init__(self, repository: InstallationRecordRepository | None = None):
        self._installation_service = InstallationService(repository)
        self._scanning = False
        self._scan_progress = 0

    @property
    def installation_service(self) -> InstallationService:
        """Get the installation service."""
        return self._installation_service

    def install_application(self, record: Any) -> tuple[bool, str]:
        """Install an application from various input types.

        Args:
            record: Can be an InstallationRecord, dict, or path string

        Returns:
            Tuple of (success, message)
        """
        # Convert various input types to InstallationRecord
        from niruvi.desktop.installation_registry import InstallationRecord

        if isinstance(record, InstallationRecord):
            record_to_install = record
        elif isinstance(record, dict):
            try:
                record_to_install = InstallationRecord.from_dict(record)
            except Exception:
                return False, "Invalid record format"
        elif isinstance(record, str):
            # It's a path - validate it parses as a real AppImage, then create a basic record
            from niruvi.desktop.appimage_metadata import AppImageMetadata

            try:
                AppImageMetadata(record)
                record_to_install = InstallationRecord(
                    name=os.path.basename(record),
                    path=record,
                )
            except Exception:
                return False, "Invalid AppImage path"
        else:
            return False, "Unsupported record type"

        success = self._installation_service.install_record(record_to_install)
        if success:
            return True, "Application installed successfully"
        else:
            return False, "Failed to install application"

    def uninstall_application(self, name: str) -> tuple[bool, str]:
        """Uninstall an application by name.

        Args:
            name: The application name to uninstall

        Returns:
            Tuple of (success, message)
        """
        success = self._installation_service.uninstall_record(name)
        if success:
            return True, f"Application '{name}' uninstalled successfully"
        else:
            return False, f"Failed to uninstall application '{name}'"

    def scan_all_installed(self, progress_callback: Callable | None = None) -> list[dict[str, Any]]:
        """Scan all installed applications for security issues.

        Args:
            progress_callback: Optional callback(progress_percent) for UI updates

        Returns:
            List of scan results
        """
        return self._installation_service.scan_installed(progress_callback)

    def verify_application(self, name: str) -> tuple[bool, str]:
        """Verify an application's integrity.

        Args:
            name: The application name to verify

        Returns:
            Tuple of (is_valid, message)
        """
        return self._installation_service.verify_installation(name)

    def get_application_path(self, name: str) -> Path | None:
        """Get the install path for an application.

        Args:
            name: The application name

        Returns:
            Path object or None
        """
        return self._installation_service.get_installation_path(name)

    def get_application_display_name(self, name: str) -> str:
        """Get the display name for an application.

        Args:
            name: The registry name

        Returns:
            Display name
        """
        return self._installation_service.get_display_name(name)

    def get_installed_applications(self) -> list[dict[str, Any]]:
        """Get all installed applications with their data.

        Returns:
            List of dictionaries with application information
        """
        records = self._repository.get_all()
        results = []
        for record in records:
            results.append(
                {
                    "name": record.name,
                    "path": record.path,
                    "version": record.version,
                    "install_date": record.install_date,
                    "auto_update": record.auto_update,
                    "update_channel": record.update_channel,
                }
            )
        return results

    @property
    def _repository(self) -> InstallationRecordRepository:
        """Get the underlying repository."""
        return self._installation_service._repository
