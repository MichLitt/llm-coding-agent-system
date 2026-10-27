"""Provider-reported LLM token usage and cost accounting.

Billed usage is tracked separately from ``MessageHistory.total_tokens``, which
is a context-window estimate used by truncation. Usage is attributed to a run by
snapshotting the client's cumulative totals at run start; this relies on one
active run per ``LLMClient``, which ``cli.factory.make_agent`` guarantees.

Status values:
  "provider"    — every LLM call returned provider usage
  "partial"     — some calls returned usage; token totals are a lower bound
  "unavailable" — no call returned usage; token totals and cost are None
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from coder_agent.config import ModelPricing

USAGE_STATUS_PROVIDER = "provider"
USAGE_STATUS_PARTIAL = "partial"
USAGE_STATUS_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class LLMUsage:
    """Usage reported by the provider for one completion request."""

    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "LLMUsage | None":
        if not isinstance(raw, dict):
            return None
        try:
            return cls(
                input_tokens=int(raw["input_tokens"]),
                output_tokens=int(raw["output_tokens"]),
                cached_input_tokens=int(raw.get("cached_input_tokens", 0) or 0),
            )
        except (KeyError, TypeError, ValueError):
            return None


def _field(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _int_or_none(value: Any) -> int | None:
    # Guard against mocks and non-numeric placeholders in provider payloads.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def usage_from_openai(raw: Any) -> LLMUsage | None:
    """Normalize an OpenAI-compatible ``usage`` object (stream or non-stream)."""
    prompt = _int_or_none(_field(raw, "prompt_tokens"))
    completion = _int_or_none(_field(raw, "completion_tokens"))
    if prompt is None or completion is None:
        return None
    cached = _int_or_none(_field(_field(raw, "prompt_tokens_details"), "cached_tokens")) or 0
    return LLMUsage(input_tokens=prompt, output_tokens=completion, cached_input_tokens=cached)


def usage_from_anthropic(raw: Any) -> LLMUsage | None:
    """Normalize an Anthropic ``Message.usage`` object.

    Anthropic reports uncached, cache-write and cache-read input separately;
    ``input_tokens`` here is their sum so both transports mean "prompt size".
    """
    uncached = _int_or_none(_field(raw, "input_tokens"))
    output = _int_or_none(_field(raw, "output_tokens"))
    if uncached is None or output is None:
        return None
    cache_write = _int_or_none(_field(raw, "cache_creation_input_tokens")) or 0
    cache_read = _int_or_none(_field(raw, "cache_read_input_tokens")) or 0
    return LLMUsage(
        input_tokens=uncached + cache_write + cache_read,
        output_tokens=output,
        cached_input_tokens=cache_read,
    )


@dataclass
class UsageTotals:
    """Cumulative usage over a sequence of LLM calls."""

    llm_calls: int = 0
    reported_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0

    def record(self, usage: LLMUsage | None) -> None:
        self.llm_calls += 1
        if usage is None:
            return
        self.reported_calls += 1
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.cached_input_tokens += usage.cached_input_tokens

    def copy(self) -> "UsageTotals":
        return UsageTotals(**self.__dict__)

    def __add__(self, other: "UsageTotals") -> "UsageTotals":
        return UsageTotals(**{key: value + getattr(other, key) for key, value in self.__dict__.items()})

    def __sub__(self, other: "UsageTotals") -> "UsageTotals":
        return UsageTotals(**{key: max(0, value - getattr(other, key)) for key, value in self.__dict__.items()})

    @property
    def status(self) -> str:
        if self.reported_calls == 0:
            return USAGE_STATUS_UNAVAILABLE
        if self.reported_calls < self.llm_calls:
            return USAGE_STATUS_PARTIAL
        return USAGE_STATUS_PROVIDER

    @property
    def total_tokens(self) -> int | None:
        if self.reported_calls == 0:
            return None
        return self.input_tokens + self.output_tokens

    def estimated_cost_usd(self, pricing: ModelPricing | None) -> float | None:
        if pricing is None or self.reported_calls == 0:
            return None
        cached_rate = pricing.cached_input_usd_per_mtok
        if cached_rate is None:
            cached_rate = pricing.input_usd_per_mtok
        uncached_input = self.input_tokens - self.cached_input_tokens
        cost = (
            uncached_input * pricing.input_usd_per_mtok
            + self.cached_input_tokens * cached_rate
            + self.output_tokens * pricing.output_usd_per_mtok
        ) / 1_000_000
        return round(cost, 6)

    def to_state(self) -> dict[str, int]:
        """Raw counters for checkpoint serialization."""
        return dict(self.__dict__)

    @classmethod
    def from_state(cls, raw: Any) -> "UsageTotals":
        if not isinstance(raw, dict):
            return cls()
        return cls(**{key: int(raw.get(key, 0) or 0) for key in cls().__dict__})

    def to_report(self, pricing: ModelPricing | None = None) -> dict[str, Any]:
        """Public summary; token fields are None when no call reported usage."""
        reported = self.reported_calls > 0
        return {
            "llm_calls": self.llm_calls,
            "usage_reported_calls": self.reported_calls,
            "token_usage_status": self.status,
            "input_tokens": self.input_tokens if reported else None,
            "output_tokens": self.output_tokens if reported else None,
            "cached_input_tokens": self.cached_input_tokens if reported else None,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": self.estimated_cost_usd(pricing),
        }


def client_usage_snapshot(client: Any) -> UsageTotals | None:
    """Copy of the client's cumulative usage, or None for clients that do not track it."""
    totals = getattr(client, "usage", None)
    if not isinstance(totals, UsageTotals):
        return None
    return totals.copy()


def client_pricing(client: Any) -> ModelPricing | None:
    pricing = getattr(getattr(client, "profile", None), "pricing", None)
    return pricing if isinstance(pricing, ModelPricing) else None


def usage_since(client: Any, start: UsageTotals | None) -> UsageTotals:
    current = client_usage_snapshot(client)
    if current is None:
        return UsageTotals()
    return current - (start or UsageTotals())


def run_usage(state: Any) -> UsageTotals:
    """Usage for one run: segments before a resume plus calls since this segment started."""
    prior = getattr(state, "prior_usage", None) or UsageTotals()
    return prior + usage_since(getattr(state, "usage_client", None), getattr(state, "usage_start", None))
