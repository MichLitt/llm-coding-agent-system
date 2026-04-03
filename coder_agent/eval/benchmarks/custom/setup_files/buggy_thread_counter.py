# buggy_thread_counter.py — SharedCounter has a race condition; agent must fix it

import threading


class SharedCounter:
    """A counter that can be incremented from multiple threads."""

    def __init__(self):
        self.value = 0
        # Bug: no Lock protecting self.value

    def increment(self):
        current = self.value
        # Simulate some work (makes the race more visible in tests)
        self.value = current + 1

    def get(self):
        return self.value


def run_concurrent_increments(n_threads: int, increments_per_thread: int) -> int:
    """Spawn n_threads, each calling counter.increment() increments_per_thread times."""
    counter = SharedCounter()
    threads = [
        threading.Thread(target=lambda: [counter.increment() for _ in range(increments_per_thread)])
        for _ in range(n_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return counter.get()
