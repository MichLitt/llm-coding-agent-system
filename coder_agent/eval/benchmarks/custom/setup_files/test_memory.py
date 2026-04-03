# test_memory.py — do NOT modify this file
import gc
import pytest
from leaky_cache import EventCache


def test_basic_emit_and_history():
    cache = EventCache(max_history=100)
    cache.emit("click", {"x": 1})
    cache.emit("hover", {"x": 2})
    assert cache.history_size() == 2


def test_history_bounded_by_max_history():
    """history must never exceed max_history entries."""
    cache = EventCache(max_history=10)
    for i in range(50):
        cache.emit("tick", i)
    assert cache.history_size() <= 10, \
        f"history_size() == {cache.history_size()}, expected <= 10"


def test_oldest_events_evicted():
    """Once at capacity, oldest events should be replaced."""
    cache = EventCache(max_history=5)
    for i in range(10):
        cache.emit("e", i)
    history = cache.get_history()
    payloads = [h["payload"] for h in history]
    # Most recent 5 should be retained (5–9)
    assert 9 in payloads, "Most recent event should be in history"
    assert 0 not in payloads, "Oldest event should have been evicted"


def test_subscribe_and_notify():
    received = []
    cache = EventCache()
    cache.subscribe(lambda e: received.append(e["type"]))
    cache.emit("alpha")
    cache.emit("beta")
    assert received == ["alpha", "beta"]


def test_unsubscribe_removes_listener():
    """Listeners should be removable to prevent memory leaks."""
    cache = EventCache()
    assert hasattr(cache, "unsubscribe"), "EventCache must have unsubscribe()"
    received = []
    cb = lambda e: received.append(e)
    cache.subscribe(cb)
    cache.emit("first")
    cache.unsubscribe(cb)
    cache.emit("second")
    assert len(received) == 1, "After unsubscribe, callback should not receive events"


def test_listener_count_after_unsubscribe():
    cache = EventCache()
    cb1 = lambda e: None
    cb2 = lambda e: None
    cache.subscribe(cb1)
    cache.subscribe(cb2)
    assert cache.listener_count() == 2
    cache.unsubscribe(cb1)
    assert cache.listener_count() == 1


def test_sustained_use_bounded_memory():
    """Emit 10000 events; history must stay at max_history."""
    cache = EventCache(max_history=100)
    for i in range(10_000):
        cache.emit("event", i)
    assert cache.history_size() == 100
