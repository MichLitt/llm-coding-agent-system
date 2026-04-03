# api_client_stub.py — HTTP API client with NO error handling; agent must add it

import urllib.request
import urllib.error
import json
from typing import Any


class APIClient:
    """Minimal HTTP client for a JSON REST API.

    Currently missing:
    - Exception handling for network errors (urllib.error.URLError)
    - Exception handling for HTTP errors (urllib.error.HTTPError)
    - Retry logic for transient failures (5xx status codes)
    - Timeout on requests
    - Raising a meaningful exception when the server returns a non-2xx status

    The agent must add proper error handling so the tests pass.
    """

    BASE_URL = "https://api.example.com"
    MAX_RETRIES = 3
    TIMEOUT = 5.0

    def __init__(self, base_url: str = BASE_URL):
        self.base_url = base_url.rstrip("/")

    def get(self, path: str) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    def post(self, path: str, data: dict) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        payload = json.dumps(data).encode()
        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())


class APIError(Exception):
    """Raised when the API returns a non-2xx status or a network error occurs."""
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code
