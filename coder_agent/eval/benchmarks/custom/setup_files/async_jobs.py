"""Async job queue implementation."""
import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional


@dataclass
class Job:
    """Represents a job in the queue."""
    job_id: str
    func: Callable
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)
    result: Any = None
    error: Optional[Exception] = None
    future: Optional[asyncio.Future] = field(default=None)


class AsyncJobQueue:
    """An asyncio-based in-memory job queue."""

    def __init__(self, max_workers: int = 1, max_size: int = 0):
        """Initialize the job queue.
        
        Args:
            max_workers: Maximum number of concurrent workers.
            max_size: Maximum queue size (0 = unlimited).
        """
        self._max_workers = max_workers
        self._max_size = max_size
        self._jobs: Dict[str, Job] = {}
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_size if max_size > 0 else 0)
        self._workers: list = []
        self._is_running = False
        self._processed_count = 0
        self._lock = asyncio.Lock()
        self._shutdown_event = asyncio.Event()

    @property
    def is_running(self) -> bool:
        """Check if the queue is running."""
        return self._is_running

    @property
    def processed_count(self) -> int:
        """Get the number of processed jobs."""
        return self._processed_count

    async def start(self) -> None:
        """Start the job queue workers."""
        if self._is_running:
            return
        
        self._is_running = True
        self._shutdown_event.clear()
        
        for _ in range(self._max_workers):
            worker = asyncio.create_task(self._worker())
            self._workers.append(worker)

    async def _worker(self) -> None:
        """Worker coroutine that processes jobs from the queue."""
        while self._is_running or not self._queue.empty():
            try:
                job = await asyncio.wait_for(self._queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue

            try:
                if asyncio.iscoroutinefunction(job.func):
                    result = await job.func(*job.args, **job.kwargs)
                else:
                    result = job.func(*job.args, **job.kwargs)
                job.result = result
            except Exception as e:
                job.error = e

            async with self._lock:
                self._processed_count += 1

            self._queue.task_done()

    async def enqueue(self, job_id: str, func: Callable, *args, **kwargs) -> Job:
        """Add a job to the queue.
        
        Args:
            job_id: Unique identifier for the job.
            func: Function to execute.
            *args: Positional arguments for the function.
            **kwargs: Keyword arguments for the function.
            
        Returns:
            The Job object.
        """
        job = Job(job_id=job_id, func=func, args=args, kwargs=kwargs)
        self._jobs[job_id] = job
        
        await self._queue.put(job)
        return job

    async def shutdown(self, wait: bool = True) -> None:
        """Shutdown the job queue.
        
        Args:
            wait: If True, wait for all queued jobs to complete.
        """
        self._is_running = False
        
        if wait:
            await self._queue.join()
        
        for worker in self._workers:
            worker.cancel()
        
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        self._shutdown_event.set()

    def get_job(self, job_id: str) -> Optional[Job]:
        """Get a job by its ID.
        
        Args:
            job_id: The job identifier.
            
        Returns:
            The Job object or None if not found.
        """
        return self._jobs.get(job_id)

    def get_queue_size(self) -> int:
        """Get the current number of pending jobs in the queue."""
        return self._queue.qsize()


async def create_job_queue(max_workers: int = 1, max_size: int = 0) -> AsyncJobQueue:
    """Create and start a new job queue.
    
    Args:
        max_workers: Maximum number of concurrent workers.
        max_size: Maximum queue size (0 = unlimited).
        
    Returns:
        A started AsyncJobQueue instance.
    """
    queue = AsyncJobQueue(max_workers=max_workers, max_size=max_size)
    await queue.start()
    return queue
