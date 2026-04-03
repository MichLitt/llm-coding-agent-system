# test_queue_impl.py — do NOT modify this file
import pytest
from partial_queue import BoundedQueue, QueueFullError, QueueEmptyError


def test_enqueue_and_dequeue():
    q = BoundedQueue(maxsize=5)
    q.enqueue(1)
    q.enqueue(2)
    assert q.dequeue() == 1
    assert q.dequeue() == 2


def test_fifo_order():
    q = BoundedQueue(maxsize=10)
    for i in range(5):
        q.enqueue(i)
    for i in range(5):
        assert q.dequeue() == i


def test_is_empty_initially():
    q = BoundedQueue(maxsize=3)
    assert q.is_empty()
    assert not q.is_full()
    assert q.size() == 0


def test_is_full_at_capacity():
    q = BoundedQueue(maxsize=2)
    q.enqueue("a")
    q.enqueue("b")
    assert q.is_full()
    assert not q.is_empty()


def test_enqueue_raises_when_full():
    q = BoundedQueue(maxsize=1)
    q.enqueue("only")
    with pytest.raises(QueueFullError):
        q.enqueue("overflow")


def test_dequeue_raises_when_empty():
    q = BoundedQueue(maxsize=5)
    with pytest.raises(QueueEmptyError):
        q.dequeue()


def test_peek_does_not_remove():
    q = BoundedQueue(maxsize=5)
    q.enqueue(99)
    assert q.peek() == 99
    assert q.size() == 1


def test_peek_raises_when_empty():
    q = BoundedQueue(maxsize=5)
    with pytest.raises(QueueEmptyError):
        q.peek()


def test_clear_empties_queue():
    q = BoundedQueue(maxsize=5)
    for i in range(5):
        q.enqueue(i)
    q.clear()
    assert q.is_empty()
    assert q.size() == 0


def test_len():
    q = BoundedQueue(maxsize=5)
    q.enqueue(1)
    q.enqueue(2)
    assert len(q) == 2


def test_invalid_maxsize():
    with pytest.raises(ValueError):
        BoundedQueue(maxsize=0)
    with pytest.raises(ValueError):
        BoundedQueue(maxsize=-1)


def test_size_tracking():
    q = BoundedQueue(maxsize=10)
    for i in range(7):
        q.enqueue(i)
    assert q.size() == 7
    q.dequeue()
    assert q.size() == 6
