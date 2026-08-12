"""EvalOpsClient — fire-and-forget submission of AgentRunReport objects.

The client never raises: all errors are logged at WARNING level and swallowed
so that a misconfigured or unreachable EvalOps endpoint cannot break an
agent run.

Configuration is read from environment variables:

- ``EVALOPS_ENDPOINT`` — full URL to POST to (e.g. http://localhost:8000/v1/ingest/agent/v1).
- ``EVALOPS_API_KEY``  — optional Bearer token.

When ``EVALOPS_ENDPOINT`` is not set the client is a no-op.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os

from coder_agent.evalops.schema import AgentRunReport

logger = logging.getLogger(__name__)


class EvalOpsClient:
    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._endpoint = endpoint or ""
        self._api_key = api_key or ""

    @classmethod
    def from_env(cls) -> "EvalOpsClient":
        return cls(
            endpoint=os.environ.get("EVALOPS_ENDPOINT"),
            api_key=os.environ.get("EVALOPS_API_KEY"),
        )

    def submit(self, report: AgentRunReport) -> None:
        try:
            self._do_submit(report)
        except Exception as exc:
            logger.warning("EvalOpsClient.submit failed (ignored): %s", exc)

    def _do_submit(self, report: AgentRunReport) -> None:
        if not self._endpoint:
            logger.debug("EvalOpsClient: no endpoint configured, skipping submit")
            return

        import urllib.request

        payload = dataclasses.asdict(report)
        data = json.dumps(payload, ensure_ascii=False, default=str).encode()
        req = urllib.request.Request(
            self._endpoint,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        if self._api_key:
            req.add_header("Authorization", f"Bearer {self._api_key}")

        with urllib.request.urlopen(req, timeout=5) as _resp:
            pass

        logger.info(
            "EvalOpsClient: submitted run %r to %s", report.run_id, self._endpoint
        )
