"""Versioned task and complexity-profile schema for ComplexCodeBench v1."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


SCHEMA_VERSION = "complex-code-eval/v1"
_DIMENSIONS = {"modules", "state", "concurrency", "migration", "recovery", "compatibility", "verification"}


@dataclass(frozen=True)
class ComplexityProfile:
    dimensions: dict[str, int]
    rationale: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ComplexityProfile":
        dimensions = raw.get("dimensions")
        if not isinstance(dimensions, dict) or not dimensions or not set(dimensions) <= _DIMENSIONS:
            raise ValueError("complexity_profile.dimensions must name known dimensions")
        if any(isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 3 for value in dimensions.values()):
            raise ValueError("complexity dimensions must be integer scores from 0 to 3")
        rationale = raw.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            raise ValueError("complexity_profile requires a rationale")
        return cls(dict(dimensions), rationale)


@dataclass(frozen=True)
class ComplexCodeTaskSpec:
    task_id: str
    description: str
    setup_files: list[str]
    verification: list[dict[str, Any]]
    complexity_profile: ComplexityProfile
    max_steps: int = 30
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ComplexCodeTaskSpec":
        required = ("task_id", "description")
        if any(not isinstance(raw.get(key), str) or not raw[key].strip() for key in required):
            raise ValueError("complex task requires task_id and description")
        setup, verification = raw.get("setup_files", []), raw.get("verification", [])
        if not isinstance(setup, list) or not all(isinstance(item, str) and item for item in setup):
            raise ValueError("complex task setup_files must be paths")
        if not isinstance(verification, list) or not verification or any(not isinstance(item, dict) for item in verification):
            raise ValueError("complex task requires executable verification")
        steps = raw.get("max_steps", 30)
        if isinstance(steps, bool) or not isinstance(steps, int) or steps < 1:
            raise ValueError("complex task max_steps must be positive")
        return cls(raw["task_id"], raw["description"], setup, verification, ComplexityProfile.from_dict(raw.get("complexity_profile", {})), steps, dict(raw.get("metadata", {})))

    def snapshot(self) -> dict[str, Any]: return asdict(self)
