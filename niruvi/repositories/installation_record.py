"""Repository for InstallationRecord entities.

Extends the base InstallationRecordRepository with additional
functionality specific to the Niruvi application.
"""

import logging

from niruvi.desktop.installation_registry import InstallationRecord, InstallationRegistry
from niruvi.repositories import InstallationRecordRepository as BaseInstallationRecordRepository

logger = logging.getLogger(__name__)


class InstallationRecordRepository(BaseInstallationRecordRepository):
    """Repository for InstallationRecord entities using SQLite backend.

    Implements the repository pattern to abstract data access,
    enabling dependency injection and testability.
    """

    def __init__(self, registry: InstallationRegistry | None = None):
        super().__init__(registry)

    def get_by_prefix(self, prefix: str) -> list[InstallationRecord]:
        """Get all records whose name starts with the given prefix."""
        all_records = self.get_all()
        return [r for r in all_records if r.name.startswith(prefix)]

    def get_recent(self, limit: int = 10) -> list[InstallationRecord]:
        """Get the most recently installed applications.

        Args:
            limit: Maximum number of records to return

        Returns:
            List of recently installed InstallationRecord objects,
            sorted by install_date descending
        """
        all_records = self.get_all()
        # Sort by install_date descending
        sorted_records = sorted(all_records, key=lambda r: r.install_date, reverse=True)
        return sorted_records[:limit]

    def search(self, query: str) -> list[InstallationRecord]:
        """Search for applications by name or path.

        Args:
            query: Search query string

        Returns:
            List of matching InstallationRecord objects
        """
        all_records = self.get_all()
        query_lower = query.lower()
        results = []
        for record in all_records:
            if query_lower in record.name.lower() or query_lower in record.path.lower():
                results.append(record)
        return results

    def migrate_from_legacy(self, legacy_path: str) -> int:
        """Migrate records from legacy JSON registry to SQLite.

        Args:
            legacy_path: Path to the legacy registry.json file

        Returns:
            Number of records migrated
        """
        import json

        try:
            with open(legacy_path) as f:
                legacy_data = json.load(f)

            records_imported = 0
            if isinstance(legacy_data, dict) and "records" in legacy_data:
                records = legacy_data["records"]
            elif isinstance(legacy_data, list):
                records = legacy_data
            else:
                return 0

            for record_dict in records:
                try:
                    record = InstallationRecord.from_dict(record_dict)
                    self.add(record)
                    records_imported += 1
                except Exception:
                    # Skip malformed records
                    continue

            return records_imported
        except Exception as e:
            logger.error("Failed to migrate legacy registry: %s", e, exc_info=True)
            return 0
