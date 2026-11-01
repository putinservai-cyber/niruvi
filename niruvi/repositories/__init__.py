"""Repository pattern for database access.

Provides a clean abstraction layer over the SQLite installation registry,
allowing for dependency injection and testability.
"""

import sqlite3
from typing import Any

from niruvi.desktop.installation_registry import InstallationRecord, InstallationRegistry


class InstallationRecordRepository:
    """Repository for InstallationRecord entities using SQLite backend.

    Implements the repository pattern to abstract data access,
    enabling dependency injection and testability.
    """

    def __init__(self, registry: InstallationRegistry | None = None):
        self._registry = registry or InstallationRegistry()

    def add(self, record: InstallationRecord) -> None:
        """Add a new installed application record."""
        self._registry.add(record)

    def remove(self, name: str) -> None:
        """Remove an installed application by name."""
        self._registry.remove(name)

    def get(self, name: str) -> InstallationRecord | None:
        """Get an installed application by name."""
        return self._registry.get(name)

    def get_all(self) -> list[InstallationRecord]:
        """Get all installed application records."""
        return self._registry.get_all()

    def lookup_by_path(self, path: str) -> InstallationRecord | None:
        """Look up an installed application by its file path."""
        return self._registry.lookup_by_path(path)

    def lookup_by_name(self, name: str) -> InstallationRecord | None:
        """Look up an installed application by name."""
        return self._registry.lookup_by_name(name)

    def contains(self, name: str) -> bool:
        """Check if an application is registered."""
        return self._registry.get(name) is not None

    def count(self) -> int:
        """Get the number of registered installations."""
        return len(self.get_all())

    def flush(self) -> None:
        """Flush pending changes to the database."""
        self._registry.flush()

    def clear(self) -> None:
        """Clear all records from the registry."""
        self._registry.reset()

    def execute(self, sql: str, params: tuple = ()) -> Any:
        """Execute a raw SQL query with parameters.

        Args:
            sql: The SQL query string
            params: Query parameters

        Returns:
            Query results
        """
        conn = self._registry._connect()
        try:
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor.fetchall()
        except sqlite3.Error:
            conn.rollback()
            raise
        finally:
            try:
                conn.close()
            except sqlite3.Error:
                pass
