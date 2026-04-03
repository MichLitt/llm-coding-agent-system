# sync_fetcher.py — callback-based fetcher; agent must refactor to async/await

import threading
from typing import Callable, Any


class DataFetcher:
    """Fetches data from multiple sources using callbacks.

    The agent must refactor this to use async/await (asyncio) instead of threads
    and callbacks, producing an AsyncDataFetcher class in the same file.

    The new AsyncDataFetcher must:
      - Have an async method `fetch_all(sources)` that fetches all sources concurrently
      - Have an async method `fetch_one(source)` that fetches a single source
      - Use asyncio.gather for concurrency (not threads)
    """

    def __init__(self, fetch_fn: Callable[[str], Any]):
        self._fetch_fn = fetch_fn

    def fetch_one(self, source: str, callback: Callable[[Any, Exception | None], None]) -> None:
        """Fetch source in a background thread; call callback(result, error)."""
        def _run():
            try:
                result = self._fetch_fn(source)
                callback(result, None)
            except Exception as exc:
                callback(None, exc)

        t = threading.Thread(target=_run)
        t.start()
        t.join()

    def fetch_all(self, sources: list[str], callback: Callable[[list[Any]], None]) -> None:
        """Fetch all sources with threads; call callback with list of results."""
        results = [None] * len(sources)
        errors = []
        lock = threading.Lock()

        def _run(idx, source):
            try:
                result = self._fetch_fn(source)
                with lock:
                    results[idx] = result
            except Exception as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=_run, args=(i, s)) for i, s in enumerate(sources)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        if errors:
            raise errors[0]
        callback(results)
