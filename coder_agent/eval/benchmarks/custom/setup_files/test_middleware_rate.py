# test_middleware_rate.py — do NOT modify this file
import time
import threading
import pytest
from middleware import RateLimitMiddleware, LoggingMiddleware, TimingMiddleware, make_simple_app


def make_environ(remote_addr: str = "127.0.0.1", path: str = "/") -> dict:
    return {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": path,
        "REMOTE_ADDR": remote_addr,
    }


def call_app(middleware, environ):
    """Invoke middleware and return (status, headers, body)."""
    captured = {}

    def start_response(status, headers, exc_info=None):
        captured["status"] = status
        captured["headers"] = dict(headers)

    body = middleware(environ, start_response)
    return captured.get("status", ""), captured.get("headers", {}), b"".join(body)


def test_rate_limit_allows_within_capacity():
    app = RateLimitMiddleware(make_simple_app(), rate=10, capacity=5)
    for _ in range(5):
        status, _, _ = call_app(app, make_environ())
        assert status.startswith("200")


def test_rate_limit_blocks_when_exceeded():
    app = RateLimitMiddleware(make_simple_app(), rate=1, capacity=3)
    results = []
    for _ in range(10):
        status, _, _ = call_app(app, make_environ())
        results.append(status)
    assert any(s.startswith("429") for s in results), \
        "Expected at least one 429 when capacity exceeded"


def test_rate_limit_per_client():
    """Different IPs should have independent buckets."""
    app = RateLimitMiddleware(make_simple_app(), rate=1, capacity=2)
    # Drain client A
    for _ in range(5):
        call_app(app, make_environ("10.0.0.1"))
    # Client B should still be allowed
    status, _, _ = call_app(app, make_environ("10.0.0.2"))
    assert status.startswith("200")


def test_rate_limit_refills_over_time():
    app = RateLimitMiddleware(make_simple_app(), rate=10, capacity=2)
    # Drain the bucket
    call_app(app, make_environ())
    call_app(app, make_environ())
    # Wait for refill (rate=10 means 0.1s to refill 1 token)
    time.sleep(0.15)
    status, _, _ = call_app(app, make_environ())
    assert status.startswith("200"), "Bucket should have refilled"


def test_429_response_body():
    app = RateLimitMiddleware(make_simple_app(), rate=0.01, capacity=1)
    call_app(app, make_environ())  # drain
    status, _, body = call_app(app, make_environ())
    assert status.startswith("429")
    assert len(body) > 0  # some body expected


def test_thread_safe_rate_limiting():
    """Concurrent requests from same IP must not exceed capacity."""
    app = RateLimitMiddleware(make_simple_app(), rate=100, capacity=5)
    results = []
    lock = threading.Lock()

    def do_request():
        status, _, _ = call_app(app, make_environ("1.2.3.4"))
        with lock:
            results.append(status)

    threads = [threading.Thread(target=do_request) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ok = sum(1 for s in results if s.startswith("200"))
    assert ok <= 5, f"Too many requests allowed: {ok}"


def test_existing_middleware_unchanged():
    """LoggingMiddleware and TimingMiddleware must still work."""
    app = TimingMiddleware(LoggingMiddleware(make_simple_app()))
    status, headers, _ = call_app(app, make_environ())
    assert status.startswith("200")
    assert "X-Duration-Ms" in headers
