# test_thread_counter.py — do NOT modify this file
import threading
from buggy_thread_counter import SharedCounter, run_concurrent_increments


def test_single_thread_correctness():
    c = SharedCounter()
    for _ in range(100):
        c.increment()
    assert c.get() == 100


def test_two_threads_no_lost_updates():
    result = run_concurrent_increments(n_threads=2, increments_per_thread=500)
    assert result == 1000, f"Expected 1000, got {result} (lost updates indicate race condition)"


def test_many_threads_no_lost_updates():
    result = run_concurrent_increments(n_threads=10, increments_per_thread=200)
    assert result == 2000, f"Expected 2000, got {result}"


def test_counter_starts_at_zero():
    c = SharedCounter()
    assert c.get() == 0


def test_repeated_runs_consistent():
    """Multiple runs with the same parameters should all yield the same result."""
    expected = 50 * 100
    for _ in range(5):
        result = run_concurrent_increments(n_threads=50, increments_per_thread=100)
        assert result == expected, f"Expected {expected}, got {result}"
