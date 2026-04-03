# test_api_client.py — do NOT modify this file
import json
import pytest
import urllib.error
from unittest.mock import patch, MagicMock
from api_client_stub import APIClient, APIError


def _make_http_error(code: int, msg: str = "Error") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url="http://x", code=code, msg=msg, hdrs=None, fp=None)


def _make_url_error() -> urllib.error.URLError:
    return urllib.error.URLError("connection refused")


def test_get_success():
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"id": 1}).encode()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = APIClient().get("/users/1")
    assert result == {"id": 1}


def test_get_raises_api_error_on_404():
    with patch("urllib.request.urlopen", side_effect=_make_http_error(404)):
        with pytest.raises(APIError) as exc_info:
            APIClient().get("/not-found")
    assert exc_info.value.status_code == 404


def test_get_raises_api_error_on_500():
    with patch("urllib.request.urlopen", side_effect=_make_http_error(500)):
        with pytest.raises(APIError) as exc_info:
            APIClient().get("/broken")
    assert exc_info.value.status_code == 500


def test_get_raises_api_error_on_network_failure():
    with patch("urllib.request.urlopen", side_effect=_make_url_error()):
        with pytest.raises(APIError):
            APIClient().get("/anything")


def test_post_raises_api_error_on_401():
    with patch("urllib.request.urlopen", side_effect=_make_http_error(401)):
        with pytest.raises(APIError) as exc_info:
            APIClient().post("/secure", {"key": "val"})
    assert exc_info.value.status_code == 401


def test_retry_on_transient_error():
    """5xx errors should be retried up to MAX_RETRIES times before raising."""
    call_count = {"n": 0}
    def side_effect(*args, **kwargs):
        call_count["n"] += 1
        raise _make_http_error(503)
    with patch("urllib.request.urlopen", side_effect=side_effect):
        with pytest.raises(APIError):
            APIClient().get("/flaky")
    assert call_count["n"] == APIClient.MAX_RETRIES


def test_timeout_is_passed_to_urlopen():
    captured = {}
    mock_resp = MagicMock()
    mock_resp.read.return_value = b'"{}"'
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    def side_effect(req, timeout=None):
        captured["timeout"] = timeout
        return mock_resp
    with patch("urllib.request.urlopen", side_effect=side_effect):
        APIClient().get("/ping")
    assert captured.get("timeout") == APIClient.TIMEOUT
