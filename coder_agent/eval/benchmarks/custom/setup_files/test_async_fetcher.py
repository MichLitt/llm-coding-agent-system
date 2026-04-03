# test_async_fetcher.py — do NOT modify this file
import asyncio
import time
import pytest
from sync_fetcher import AsyncDataFetcher


async def mock_fetch(source: str) -> str:
    """Simulates a fast async fetch."""
    return f"data:{source}"


async def slow_fetch(source: str) -> str:
    """Simulates a 0.1s network call."""
    await asyncio.sleep(0.1)
    return f"data:{source}"


async def failing_fetch(source: str) -> str:
    raise ValueError(f"Failed to fetch {source}")


def test_fetch_one_returns_result():
    result = asyncio.run(AsyncDataFetcher(mock_fetch).fetch_one("s1"))
    assert result == "data:s1"


def test_fetch_all_returns_all():
    fetcher = AsyncDataFetcher(mock_fetch)
    results = asyncio.run(fetcher.fetch_all(["a", "b", "c"]))
    assert results == ["data:a", "data:b", "data:c"]


def test_fetch_all_concurrent_speed():
    """5 slow fetches should complete in ~0.1s with asyncio.gather."""
    fetcher = AsyncDataFetcher(slow_fetch)
    sources = [f"s{i}" for i in range(5)]
    start = time.time()
    results = asyncio.run(fetcher.fetch_all(sources))
    elapsed = time.time() - start
    assert len(results) == 5
    assert elapsed < 0.4, f"fetch_all took {elapsed:.2f}s — expected concurrent"


def test_fetch_one_propagates_error():
    fetcher = AsyncDataFetcher(failing_fetch)
    with pytest.raises(ValueError, match="Failed to fetch"):
        asyncio.run(fetcher.fetch_one("bad"))


def test_fetch_all_empty():
    fetcher = AsyncDataFetcher(mock_fetch)
    results = asyncio.run(fetcher.fetch_all([]))
    assert results == []


def test_async_data_fetcher_is_importable():
    """AsyncDataFetcher must exist in sync_fetcher module."""
    from sync_fetcher import AsyncDataFetcher
    assert AsyncDataFetcher is not None
