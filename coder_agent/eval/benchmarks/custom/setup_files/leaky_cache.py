# leaky_cache.py — has a memory leak; agent must find and fix it
#
# The leak: EventCache stores every emitted event in self._history with no eviction.
# Additionally, _listeners holds strong references to callbacks that are never cleaned up.
# Under sustained use, both grow without bound.
#
# Fix:
# 1. Cap _history to a configurable max_history (e.g. 1000), evict oldest entries.
# 2. Provide an unsubscribe() method (or use weakrefs) so listeners can be removed.

from typing import Any, Callable
from collections import deque


class EventCache:
    """An event bus that caches recent events and notifies listeners.

    Current problems:
    - self._history grows unboundedly (no max size)
    - self._listeners accumulates callbacks with no removal mechanism

    The agent must fix both issues so test_memory.py passes.
    """

    def __init__(self, max_history: int = 1000):
        self.max_history = max_history
        self._history: list[dict] = []          # Bug: should be bounded (use deque w/ maxlen)
        self._listeners: list[Callable] = []     # Bug: no unsubscribe

    def subscribe(self, callback: Callable[[dict], None]) -> None:
        """Register a callback to be called on every new event."""
        self._listeners.append(callback)

    def emit(self, event_type: str, payload: Any = None) -> None:
        """Emit an event, store in history, and notify all listeners."""
        event = {"type": event_type, "payload": payload}
        self._history.append(event)
        for cb in self._listeners:
            cb(event)

    def get_history(self) -> list[dict]:
        """Return stored event history."""
        return list(self._history)

    def listener_count(self) -> int:
        return len(self._listeners)

    def history_size(self) -> int:
        return len(self._history)
