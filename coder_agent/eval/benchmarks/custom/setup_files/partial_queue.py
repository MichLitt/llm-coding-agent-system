# partial_queue.py — class skeleton with TODO stubs; agent must implement them

from collections import deque
from typing import Any, Optional


class BoundedQueue:
    """A FIFO queue with a configurable maximum capacity.

    The agent must implement all methods marked TODO so that test_queue_impl.py passes.
    Do NOT change method signatures or add/remove methods.
    """

    def __init__(self, maxsize: int):
        if maxsize <= 0:
            raise ValueError("maxsize must be positive")
        self.maxsize = maxsize
        self._data: deque = deque()

    def enqueue(self, item: Any) -> None:
        """Add item to the back of the queue.

        Raises QueueFullError if the queue is at capacity.
        """
        # TODO: implement
        raise NotImplementedError

    def dequeue(self) -> Any:
        """Remove and return the front item.

        Raises QueueEmptyError if the queue is empty.
        """
        # TODO: implement
        raise NotImplementedError

    def peek(self) -> Any:
        """Return the front item WITHOUT removing it.

        Raises QueueEmptyError if the queue is empty.
        """
        # TODO: implement
        raise NotImplementedError

    def is_empty(self) -> bool:
        """Return True if the queue has no items."""
        # TODO: implement
        raise NotImplementedError

    def is_full(self) -> bool:
        """Return True if the queue is at maxsize."""
        # TODO: implement
        raise NotImplementedError

    def size(self) -> int:
        """Return the current number of items."""
        # TODO: implement
        raise NotImplementedError

    def clear(self) -> None:
        """Remove all items from the queue."""
        # TODO: implement
        raise NotImplementedError

    def __len__(self) -> int:
        """Support len(queue)."""
        # TODO: implement
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"BoundedQueue(maxsize={self.maxsize}, size={self.size()})"


class QueueFullError(Exception):
    """Raised when enqueue is called on a full BoundedQueue."""


class QueueEmptyError(Exception):
    """Raised when dequeue/peek is called on an empty BoundedQueue."""
