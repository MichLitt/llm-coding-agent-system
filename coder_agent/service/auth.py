"""Optional Bearer-token guard for Agent runtime business endpoints."""
from __future__ import annotations

import hmac
import os

from fastapi import HTTPException, Request, status


def require_api_token(request: Request) -> None:
    expected = os.environ.get("AGENT_API_TOKEN", "")
    if not expected:
        return
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer ") or not hmac.compare_digest(authorization[7:], expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API token")
