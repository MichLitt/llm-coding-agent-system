# test_downloader.py — do NOT modify this file
import asyncio
import time
import pytest
from buggy_downloader import AsyncDownloader, DownloadResult


async def instant_fetcher(url: str) -> str:
    """Returns immediately — simulates a fast network call."""
    return f"content:{url}"


async def slow_fetcher(url: str) -> str:
    """Sleeps 0.1 s — used to verify concurrency."""
    await asyncio.sleep(0.1)
    return f"content:{url}"


def test_fetch_returns_results():
    dl = AsyncDownloader()
    urls = ["http://a.com", "http://b.com", "http://c.com"]
    results = dl.fetch(urls, instant_fetcher)
    assert len(results) == 3
    assert all(isinstance(r, DownloadResult) for r in results)


def test_fetch_result_content():
    dl = AsyncDownloader()
    urls = ["http://x.com"]
    results = dl.fetch(urls, instant_fetcher)
    assert results[0].content == "content:http://x.com"
    assert results[0].url == "http://x.com"


def test_concurrent_downloads_faster_than_sequential():
    """With asyncio.gather, 5 slow fetches should finish in ~0.1s not ~0.5s."""
    dl = AsyncDownloader(max_concurrent=10)
    urls = [f"http://url{i}.com" for i in range(5)]
    start = time.time()
    results = dl.fetch(urls, slow_fetcher)
    elapsed = time.time() - start
    assert len(results) == 5
    assert elapsed < 0.4, f"Downloads took {elapsed:.2f}s — expected concurrent (<0.4s)"


def test_semaphore_respected():
    """Semaphore should be released so a second batch can run."""
    dl = AsyncDownloader(max_concurrent=2)
    urls = [f"http://url{i}.com" for i in range(4)]
    # If semaphore is never released, this will deadlock/timeout
    results = dl.fetch(urls, instant_fetcher)
    assert len(results) == 4


def test_empty_url_list():
    dl = AsyncDownloader()
    results = dl.fetch([], instant_fetcher)
    assert results == []
