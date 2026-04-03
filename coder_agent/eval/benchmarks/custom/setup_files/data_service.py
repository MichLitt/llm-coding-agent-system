# data_service.py — agent must add structured logging + timing metrics
#
# Currently: no logging, no timing, no metrics.
# Goal: add logging (structlog or stdlib logging with JSON-like format) and
#       per-operation timing so test_observability.py passes.

import time
from typing import Any, Optional


class DataServiceError(Exception):
    pass


class DataService:
    """Simple in-memory data service for CRUD operations.

    The agent must add:
    1. Structured logging on every operation (at least: operation name, key, duration_ms)
    2. A `get_metrics()` method returning call counts and average duration per operation
    3. All existing behaviour must continue to work (tests in test_observability.py)
    """

    def __init__(self):
        self._store: dict[str, Any] = {}

    def set(self, key: str, value: Any) -> None:
        """Store a value under the given key."""
        if not key:
            raise DataServiceError("Key cannot be empty")
        self._store[key] = value

    def get(self, key: str) -> Any:
        """Retrieve a value by key. Raises DataServiceError if not found."""
        if key not in self._store:
            raise DataServiceError(f"Key {key!r} not found")
        return self._store[key]

    def delete(self, key: str) -> None:
        """Remove a key. Raises DataServiceError if not found."""
        if key not in self._store:
            raise DataServiceError(f"Key {key!r} not found")
        del self._store[key]

    def list_keys(self) -> list[str]:
        """Return all stored keys."""
        return list(self._store.keys())

    def bulk_set(self, items: dict[str, Any]) -> int:
        """Set multiple key-value pairs. Returns count of items set."""
        for k, v in items.items():
            self.set(k, v)
        return len(items)
