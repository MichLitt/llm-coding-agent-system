# buggy_downloader.py — async downloader with 3 intentional async/await bugs

import asyncio
from typing import Callable


class DownloadResult:
    def __init__(self, url: str, content: str, status: int = 200):
        self.url = url
        self.content = content
        self.status = status


class AsyncDownloader:
    """Downloads multiple URLs concurrently using asyncio.

    Bug 1: fetch() is not declared as async but calls await inside a helper.
    Bug 2: download_all() uses a plain loop instead of asyncio.gather(), so
           downloads happen sequentially, not concurrently.
    Bug 3: the semaphore is acquired but never released (missing async with).
    """

    def __init__(self, max_concurrent: int = 5):
        self.max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def _fetch_one(self, url: str, fetcher: Callable) -> DownloadResult:
        # Bug 3: should use `async with self._semaphore:` but instead just acquires
        await self._semaphore.acquire()
        content = await fetcher(url)
        return DownloadResult(url=url, content=content)

    def fetch(self, urls: list[str], fetcher: Callable) -> list[DownloadResult]:
        # Bug 1: this method calls async code but is not declared async
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self._run(urls, fetcher))
        finally:
            loop.close()

    async def _run(self, urls: list[str], fetcher: Callable) -> list[DownloadResult]:
        # Bug 2: sequential loop instead of asyncio.gather
        results = []
        for url in urls:
            result = await self._fetch_one(url, fetcher)
            results.append(result)
        return results
