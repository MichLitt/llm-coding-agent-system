# middleware.py — agent must add token-bucket rate limiting to this WSGI middleware stack
#
# Current: LoggingMiddleware and TimingMiddleware already implemented.
# Missing: RateLimitMiddleware using the token-bucket algorithm.
#
# RateLimitMiddleware requirements:
#   - Per-client rate limiting keyed by IP address (environ["REMOTE_ADDR"])
#   - Token bucket: rate (tokens/s) and capacity (max burst)
#   - If tokens available: process request, deduct 1 token
#   - If no tokens: return 429 Too Many Requests immediately
#   - Thread-safe (multiple requests may arrive concurrently)

import time
import threading
import logging
from typing import Callable, Any

logger = logging.getLogger(__name__)

WsgiApp = Callable[[dict, Callable], Any]


class LoggingMiddleware:
    """Logs each request's method, path, and status code."""

    def __init__(self, app: WsgiApp):
        self.app = app

    def __call__(self, environ: dict, start_response: Callable) -> Any:
        method = environ.get("REQUEST_METHOD", "GET")
        path = environ.get("PATH_INFO", "/")
        captured = {}

        def capturing_start_response(status, headers, exc_info=None):
            captured["status"] = status
            return start_response(status, headers, exc_info)

        result = self.app(environ, capturing_start_response)
        logger.info(f"{method} {path} -> {captured.get('status', '???')}")
        return result


class TimingMiddleware:
    """Records request processing time in X-Duration-Ms response header."""

    def __init__(self, app: WsgiApp):
        self.app = app

    def __call__(self, environ: dict, start_response: Callable) -> Any:
        start = time.perf_counter()
        captured_headers = {}

        def timing_start_response(status, headers, exc_info=None):
            elapsed_ms = (time.perf_counter() - start) * 1000
            headers = list(headers) + [("X-Duration-Ms", f"{elapsed_ms:.1f}")]
            captured_headers["headers"] = headers
            return start_response(status, headers, exc_info)

        return self.app(environ, timing_start_response)


class RateLimitMiddleware:
    """Token-bucket rate limiter middleware. Agent must implement this."""

    def __init__(self, app: WsgiApp, rate: float = 10.0, capacity: float = 20.0):
        """
        Args:
            app: wrapped WSGI application
            rate: token refill rate in tokens per second
            capacity: maximum token bucket size (burst limit)
        """
        self.app = app
        self.rate = rate
        self.capacity = capacity
        # TODO: implement token bucket state and __call__

    def __call__(self, environ: dict, start_response: Callable) -> Any:
        # TODO: implement rate limiting logic
        raise NotImplementedError


def make_simple_app(status: str = "200 OK", body: str = "OK") -> WsgiApp:
    """Helper: returns a minimal WSGI app for testing."""
    def app(environ, start_response):
        start_response(status, [("Content-Type", "text/plain")])
        return [body.encode()]
    return app
