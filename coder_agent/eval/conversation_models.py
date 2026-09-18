"""Versioned, deterministic data model for ConversationBench v1."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


SCHEMA_VERSION = "conversation-eval/v1"


@dataclass(frozen=True)
class ConversationTurnSpec:
    turn_id: str
    user_message: str
    intent: str
    max_steps: int = 15
    phase_checks: list[dict[str, Any]] = field(default_factory=list)
    introduces_constraints: list[str] = field(default_factory=list)
    retains_constraints: list[str] = field(default_factory=list)
    expects_clarification: bool = False

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ConversationTurnSpec":
        required = ("turn_id", "user_message", "intent")
        if any(not isinstance(value.get(key), str) or not value[key].strip() for key in required):
            raise ValueError("conversation turn requires non-empty turn_id, user_message, and intent")
        max_steps = value.get("max_steps", 15)
        if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1:
            raise ValueError("conversation turn max_steps must be a positive integer")
        lists = {key: value.get(key, []) for key in ("phase_checks", "introduces_constraints", "retains_constraints")}
        if not isinstance(lists["phase_checks"], list) or any(not isinstance(item, dict) for item in lists["phase_checks"]):
            raise ValueError("conversation turn phase_checks must be a list of objects")
        for key in ("introduces_constraints", "retains_constraints"):
            if not isinstance(lists[key], list) or any(not isinstance(item, str) or not item for item in lists[key]):
                raise ValueError(f"conversation turn {key} must be a list of non-empty strings")
        return cls(
            turn_id=value["turn_id"], user_message=value["user_message"], intent=value["intent"],
            max_steps=max_steps, phase_checks=lists["phase_checks"],
            introduces_constraints=lists["introduces_constraints"], retains_constraints=lists["retains_constraints"],
            expects_clarification=bool(value.get("expects_clarification", False)),
        )


@dataclass(frozen=True)
class ConversationTaskSpec:
    conversation_id: str
    split: str
    category: str
    difficulty: str
    setup_files: list[str]
    turns: list[ConversationTurnSpec]
    final_checks: list[dict[str, Any]]
    verification_contract: dict[str, Any]
    max_total_steps: int
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ConversationTaskSpec":
        required = ("conversation_id", "split", "category", "difficulty")
        if any(not isinstance(value.get(key), str) or not value[key].strip() for key in required):
            raise ValueError("conversation task is missing a required string field")
        if value["split"] not in {"dev", "test"}:
            raise ValueError("conversation task split must be dev or test")
        turns = [ConversationTurnSpec.from_dict(item) for item in value.get("turns", [])]
        if not 3 <= len(turns) <= 5 or len({turn.turn_id for turn in turns}) != len(turns):
            raise ValueError("conversation task must have 3-5 turns with unique IDs")
        setup_files = value.get("setup_files", [])
        final_checks = value.get("final_checks", [])
        contract = value.get("verification_contract", {})
        if not isinstance(setup_files, list) or not all(isinstance(item, str) and item for item in setup_files):
            raise ValueError("conversation task setup_files must be strings")
        if not isinstance(final_checks, list) or any(not isinstance(item, dict) for item in final_checks):
            raise ValueError("conversation task final_checks must be objects")
        if not isinstance(contract, dict) or not isinstance(contract.get("constraint_checks", {}), dict):
            raise ValueError("conversation task verification_contract.constraint_checks must be an object")
        constraints = set().union(*(set(turn.introduces_constraints + turn.retains_constraints) for turn in turns))
        if not constraints <= set(contract["constraint_checks"]):
            raise ValueError("every conversation constraint requires a deterministic constraint_checks mapping")
        max_total_steps = value.get("max_total_steps", 60)
        if isinstance(max_total_steps, bool) or not isinstance(max_total_steps, int) or max_total_steps < 1:
            raise ValueError("conversation task max_total_steps must be a positive integer")
        return cls(value["conversation_id"], value["split"], value["category"], value["difficulty"], setup_files, turns, final_checks, contract, max_total_steps, dict(value.get("metadata", {})))

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)
