import asyncio

import pytest

from async_jobs import AsyncJobQueue, create_job_queue


@pytest.mark.asyncio
async def test_queue_starts_and_stops_cleanly():
    queue = await asyncio.wait_for(create_job_queue(max_workers=1), timeout=1.0)
    assert queue.is_running is True

    await asyncio.wait_for(queue.shutdown(wait=True), timeout=1.0)
    assert queue.is_running is False


@pytest.mark.asyncio
async def test_fifo_order_with_single_worker():
    results = []

    def add_value(value):
        results.append(value)
        return value

    queue = await asyncio.wait_for(create_job_queue(max_workers=1), timeout=1.0)
    for index in range(4):
        await asyncio.wait_for(queue.enqueue(f"job_{index}", add_value, index), timeout=1.0)

    await asyncio.sleep(0.2)
    await asyncio.wait_for(queue.shutdown(wait=True), timeout=1.0)

    assert results == [0, 1, 2, 3]
    assert queue.processed_count == 4


@pytest.mark.asyncio
async def test_async_jobs_store_results():
    async def double(value):
        await asyncio.sleep(0.01)
        return value * 2

    queue = await asyncio.wait_for(create_job_queue(max_workers=2), timeout=1.0)
    for index in range(3):
        await asyncio.wait_for(queue.enqueue(f"job_{index}", double, index), timeout=1.0)

    await asyncio.sleep(0.2)
    await asyncio.wait_for(queue.shutdown(wait=True), timeout=1.0)

    assert queue.get_job("job_0").result == 0
    assert queue.get_job("job_1").result == 2
    assert queue.get_job("job_2").result == 4


@pytest.mark.asyncio
async def test_job_errors_are_captured():
    def fail():
        raise ValueError("boom")

    queue = await asyncio.wait_for(create_job_queue(max_workers=1), timeout=1.0)
    job = await asyncio.wait_for(queue.enqueue("failing_job", fail), timeout=1.0)

    await asyncio.sleep(0.2)
    await asyncio.wait_for(queue.shutdown(wait=True), timeout=1.0)

    assert isinstance(job.error, ValueError)
    assert str(job.error) == "boom"


@pytest.mark.asyncio
async def test_queue_size_tracks_pending_jobs():
    async def slow_job():
        await asyncio.sleep(0.2)

    queue = await asyncio.wait_for(create_job_queue(max_workers=1), timeout=1.0)
    await asyncio.wait_for(queue.enqueue("job_1", slow_job), timeout=1.0)
    await asyncio.wait_for(queue.enqueue("job_2", slow_job), timeout=1.0)

    assert queue.get_queue_size() >= 1

    await asyncio.wait_for(queue.shutdown(wait=True), timeout=1.0)


@pytest.mark.asyncio
async def test_shutdown_without_wait_returns_quickly():
    async def slow_job():
        await asyncio.sleep(0.5)

    queue = await asyncio.wait_for(create_job_queue(max_workers=1), timeout=1.0)
    await asyncio.wait_for(queue.enqueue("job_1", slow_job), timeout=1.0)

    await asyncio.sleep(0.05)
    await asyncio.wait_for(queue.shutdown(wait=False), timeout=1.0)
    assert queue.is_running is False
