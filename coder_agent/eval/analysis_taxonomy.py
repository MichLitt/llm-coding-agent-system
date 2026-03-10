from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any


def has_error_type(traj: dict, error_type: str) -> bool:
    return any(s.get("error_type") == error_type for s in traj.get("steps", []))


def has_tool_error(traj: dict) -> bool:
    return any(
        "tool" in str(s.get("observation", "")).lower() and "not found" in str(s.get("observation", "")).lower()
        for s in traj.get("steps", [])
    )


def is_planning_error(traj: dict) -> bool:
    steps = traj.get("steps", [])
    if len(steps) < 3:
        return False
    tool_action_pairs = [
        (s.get("action", {}).get("tool", ""), str(s.get("action", {}).get("args", {}))[:100])
        for s in steps
        if s.get("action")
    ]
    if len(tool_action_pairs) > 2:
        for i in range(len(tool_action_pairs) - 2):
            if tool_action_pairs[i] == tool_action_pairs[i + 1] == tool_action_pairs[i + 2]:
                return True
    return False


def is_context_lost(traj: dict) -> bool:
    write_actions = [
        (s.get("action") or {}).get("args", {}).get("path", "")
        for s in traj.get("steps", [])
        if (s.get("action") or {}).get("tool") in ("write_file",)
    ]
    seen = set()
    for path in write_actions:
        if path in seen:
            return True
        seen.add(path)
    return False


TAXONOMY_RULES = [
    ("Planning Error", lambda t: is_planning_error(t)),
    ("Tool Error", lambda t: has_error_type(t, "tool_not_found") or has_tool_error(t)),
    ("Syntax Error", lambda t: has_error_type(t, "SyntaxError")),
    ("Import Error", lambda t: has_error_type(t, "ImportError")),
    ("Logic Error", lambda t: has_error_type(t, "LogicError") or has_error_type(t, "AssertionError")),
    ("Context Lost", lambda t: is_context_lost(t)),
    ("Timeout", lambda t: t.get("final_status") == "timeout"),
    ("Other", lambda t: True),
]


@dataclass
class TaxonomyResult:
    category: str
    count: int
    fraction: float
    example_task_ids: list[str] = field(default_factory=list)


def failure_taxonomy(trajs: list[dict[str, Any]]) -> list[TaxonomyResult]:
    failed = [t for t in trajs if t["final_status"] != "success"]
    if not failed:
        return []

    category_counts: Counter = Counter()
    category_examples: dict[str, list[str]] = defaultdict(list)
    for traj in failed:
        for category, rule_fn in TAXONOMY_RULES:
            if rule_fn(traj):
                category_counts[category] += 1
                if len(category_examples[category]) < 3:
                    category_examples[category].append(traj["task_id"])
                break

    total = len(failed)
    return [
        TaxonomyResult(
            category=category,
            count=count,
            fraction=count / total,
            example_task_ids=category_examples[category],
        )
        for category, count in category_counts.most_common()
    ]
