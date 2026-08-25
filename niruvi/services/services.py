"""Core service implementations for the Niruvi application.

This module provides the concrete service implementations that
mediate between the UI layer and the data access layer.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from niruvi.core.scanner import scan_directory
from niruvi.core.verification import verify_sha256
from niruvi.repositories.installation_record import InstallationRecordRepository

logger = logging.getLogger(__name__)


class LRUCache:
    """Simple LRU (Least Recently Used) cache with TTL support.

    Args:
        max_size: Maximum number of items to cache
        ttl_seconds: Time-to-live in seconds for each cache entry
    """

    def __init__(self, max_size: int = 128, ttl_seconds: int = 30):
        self._max_size = max_size
        self._ttl_seconds = ttl_seconds
        self._cache: dict[str, tuple[Any, float]] = {}
        self._access_order: list[str] = []
        self._creation_time: dict[str, float] = {}

    def get(self, key: str) -> Any | None:
        """Get a cached item if it exists and hasn't expired.

        Args:
            key: The cache key

        Returns:
            Cached value if valid, None otherwise
        """
        if key not in self._cache:
            return None

        creation_time = self._creation_time.get(key)
        if creation_time is None:
            return None

        if time.time() - creation_time > self._ttl_seconds:
            # Expired - remove it
            self.invalidate(key)
            return None

        # Update access order
        self._access_order.remove(key)
        self._access_order.append(key)
        return self._cache[key]

    def set(self, key: str, value: Any) -> None:
        """Set a cached item with TTL.

        Args:
            key: The cache key
            value: The value to cache
        """

        self._cache[key] = value
        self._creation_time[key] = time.time()
        self._access_order.append(key)

        # Enforce max size (remove oldest)
        if len(self._cache) > self._max_size:
            oldest = self._access_order.pop(0)
            self.invalidate(oldest)

    def invalidate(self, key: str) -> None:
        """Invalidate a cache entry."""
        self._cache.pop(key, None)
        self._creation_time.pop(key, None)
        self._access_order.remove(key)

    def clear(self) -> None:
        """Clear all cache entries."""
        self._cache.clear()
        self._creation_time.clear()
        self._access_order.clear()


class InstallationService:
    """Service for managing installed applications with LRU caching.

    Provides business logic for installation management with an LRU
    cache for app metadata to improve performance.
    """

    def __init__(
        self,
        repository: InstallationRecordRepository | None = None,
        metadata_cache_ttl: int = 30,
    ):
        self._repository = repository or InstallationRecordRepository()
        self._metadata_cache: LRUCache = LRUCache(max_size=64, ttl_seconds=metadata_cache_ttl)
        self._settings: Any = None

    @property
    def settings(self):
        """Get application settings."""
        if self._settings is None:
            from niruvi.config import get_settings

            self._settings = get_settings()
        return self._settings

    @settings.setter
    def settings(self, value):
        self._settings = value

    def install_application(self, record: Any) -> tuple[bool, str]:
        """Install a new application record.

        Args:
            record: InstallationRecord, dict, or path string

        Returns:
            Tuple of (success, message)
        """
        from niruvi.desktop.installation_registry import InstallationRecord

        # Convert various input types to InstallationRecord
        if isinstance(record, InstallationRecord):
            record_to_install = record
        elif isinstance(record, dict):
            try:
                record_to_install = InstallationRecord.from_dict(record)
            except Exception:
                return False, "Invalid record format"
        elif isinstance(record, str):
            try:
                # Validate the path parses as a real AppImage
                from niruvi.desktop.appimage_metadata import AppImageMetadata

                AppImageMetadata(record)
                record_to_install = InstallationRecord(
                    name=os.path.basename(record),
                    path=record,
                )
            except Exception:
                return False, "Invalid AppImage path"
        else:
            return False, "Unsupported record type"

        return self._repository_install(record_to_install)

    def _repository_install(self, record: Any) -> tuple[bool, str]:
        """Internal method to install a record using the repository."""
        try:
            # Validate the record
            if not record.name:
                return False, "Cannot install record without a name"

            # Check if already installed
            if self._repository.contains(record.name):
                return False, f"Application '{record.name}' is already installed"

            # Add the record
            self._repository.add(record)
            return True, "Application installed successfully"

        except Exception as e:
            logger.error("Failed to install application: %s", e, exc_info=True)
            return False, str(e)[:200]

    def uninstall_application(self, name: str) -> tuple[bool, str]:
        """Uninstall an application by name.

        Args:
            name: The application name to uninstall

        Returns:
            Tuple of (success, message)
        """
        try:
            record = self._repository.get(name)
            if record is None:
                return False, f"Application '{name}' is not installed"

            # Remove associated files if path exists
            if record.path and os.path.exists(record.path):
                try:
                    shutil.rmtree(record.path)
                except OSError:
                    pass

            self._repository.remove(name)
            return True, f"Application '{name}' uninstalled successfully"

        except Exception as e:
            logger.error("Failed to uninstall application: %s", e, exc_info=True)
            return False, str(e)[:200]

    def scan_installed(self, progress_callback: Callable[..., Any] | None = None) -> list[dict[str, Any]]:
        """Scan all installed applications for security issues.

        Uses LRU caching for metadata and runs scan in a structured way.

        Args:
            progress_callback: Optional callback(progress_percent)

        Returns:
            List of scan results
        """
        results: list[dict[str, Any]] = []
        all_records = self._repository.get_all()
        total = len(all_records)

        if total == 0:
            return results

        for index, record in enumerate(all_records):
            try:
                # Use cached metadata where possible
                cache_key = f"meta:{record.name}"
                cached_meta = self._metadata_cache.get(cache_key)

                if record.path and os.path.exists(record.path):
                    # Scan the directory
                    file_findings = scan_directory(record.path)
                    result: dict[str, Any] = {
                        "path": record.path,
                        "verdict": "suspicious" if file_findings else "clean",
                        "matches": file_findings,
                        "record": record,
                    }

                    # Cache metadata about the scan
                    if cached_meta is None:
                        self._metadata_cache.set(
                            cache_key, {"scanned": True, "results_count": len(file_findings)}
                        )

                    results.append(result)
                else:
                    results.append({"path": record.path or "", "verdict": "clean", "matches": [], "record": record})

                # Call progress callback
                if progress_callback and index < total - 1:
                    progress_pct = int((index + 1) / total * 100)
                    progress_callback(progress_pct)

            except Exception as e:
                logger.error("Failed to scan application '%s': %s", record.name, e, exc_info=True)
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
            name: The application name to verify

        Returns:
            Tuple of (is_valid, message)
        """
        try:
            record = self._repository.get(name)
            if record is None:
                return False, f"Application '{name}' is not installed"

            if not record.path or not os.path.exists(record.path):
                return False, f"Application '{name}' path does not exist"

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
        """Get the install path for an application."""
        record = self._repository.get(name)
        if record and record.path:
            return Path(record.path)
        return None

    def get_display_name(self, name: str) -> str:
        """Get the display name for an application."""
        record = self._repository.get(name)
        if record and record.display_name_override:
            return record.display_name_override
        return name

    def get_installed_applications(self) -> list[dict[str, Any]]:
        """Get all installed applications with their data."""
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
        """Install an application from various input types."""
        return self._installation_service.install_application(record)

    def uninstall_application(self, name: str) -> tuple[bool, str]:
        """Uninstall an application by name."""
        return self._installation_service.uninstall_application(name)

    def scan_all_installed(self, progress_callback: Callable[..., Any] | None = None) -> list[dict[str, Any]]:
        """Scan all installed applications for security issues."""
        return self._installation_service.scan_installed(progress_callback)

    def verify_application(self, name: str) -> tuple[bool, str]:
        """Verify an application's integrity."""
        return self._installation_service.verify_installation(name)

    def get_application_path(self, name: str) -> Path | None:
        """Get the install path for an application."""
        return self._installation_service.get_installation_path(name)

    def get_application_display_name(self, name: str) -> str:
        """Get the display name for an application."""
        return self._installation_service.get_display_name(name)

    def get_installed_applications(self) -> list[dict[str, Any]]:
        """Get all installed applications with their data."""
        return self._installation_service.get_installed_applications()
