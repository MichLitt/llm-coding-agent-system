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


LAYERED_FAILURE_CATEGORIES = (
    "infra_setup_failure",
    "dependency_noise",
    "tool_protocol_or_provider",
    "verification_overlay_conflict",
    "shell_exit_masking",
    "wrong_file_edit",
    "test_drift",
    "genuine_implementation_miss",
)


def _joined_failure_text(traj: dict[str, Any]) -> str:
    parts = [str(item) for item in traj.get("error_types", [])]
    parts.extend(str(step.get("observation", "")) for step in traj.get("steps", []))
    return "\n".join(parts).lower()


def _edited_paths(traj: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for step in traj.get("steps", []):
        action = step.get("action") or {}
        if action.get("tool") not in {"write_file", "patch_file"}:
            continue
        path = str((action.get("args") or {}).get("path", "")).strip()
        if path:
            paths.append(path)
    return paths


def _run_command_steps(traj: dict[str, Any]) -> list[tuple[str, str]]:
    commands: list[tuple[str, str]] = []
    for step in traj.get("steps", []):
        action = step.get("action") or {}
        if action.get("tool") != "run_command":
            continue
        args = action.get("args") or {}
        command = str(args.get("command", "")).strip()
        observation = str(step.get("observation", ""))
        if command:
            commands.append((command, observation))
    return commands


def _looks_like_masking_pipeline(command: str) -> bool:
    normalized = command.lower()
    return any(token in normalized for token in ("| tail", "| head", "| grep"))


def _looks_like_pytest_command(command: str) -> bool:
    normalized = command.lower()
    return "pytest" in normalized


def _saw_shell_exit_masking_signal(traj: dict[str, Any]) -> bool:
    commands = _run_command_steps(traj)
    last_masking_index: int | None = None
    for index, (command, observation) in enumerate(commands):
        if not _looks_like_pytest_command(command):
            continue
        if not _looks_like_masking_pipeline(command):
            continue
        if "Exit code: 0" in observation:
            last_masking_index = index
    if last_masking_index is None:
        return False

    for command, _observation in commands[last_masking_index + 1 :]:
        if not _looks_like_pytest_command(command):
            continue
        if not _looks_like_masking_pipeline(command):
            return False
    return True


def _looks_like_dependency_noise(text: str) -> bool:
    dependency_signals = (
        "pip install",
        "uv pip install",
        "ad hoc install budget exceeded",
        "module not found",
        "modulenotfounderror",
        "requires sphinx >=",
        "requires sphinx >",
        "distributionnotfound",
        "pkg_resources.distributionnotfound",
    )
    return any(signal in text for signal in dependency_signals)


def _looks_like_setup_failure(text: str) -> bool:
    setup_signals = (
        "setup command failed",
        "git clone",
        "checkout failed",
        "failed to checkout",
        "unable to clone",
        "verification command unavailable before edits",
    )
    return any(signal in text for signal in setup_signals)


def _append_secondary_edit_signals(
    *,
    secondary: list[str],
    expected_targets: set[str],
    edited_path_set: set[str],
    authorized_test_paths: set[str],
) -> None:
    if expected_targets and edited_path_set:
        off_target = sorted(path for path in edited_path_set if path not in expected_targets)
        if off_target:
            secondary.append("wrong_file_edit")
    if any(_is_test_path(path) and path not in authorized_test_paths for path in edited_path_set):
        secondary.append("test_drift")


def _primary_from_secondary(secondary: list[str]) -> tuple[str, list[str], str] | None:
    if "wrong_file_edit" in secondary:
        return "wrong_file_edit", [item for item in secondary if item != "wrong_file_edit"], "Edits drifted away from expected patch targets."
    if "test_drift" in secondary:
        return "test_drift", [item for item in secondary if item != "test_drift"], "Trajectory edited unlisted test files during recovery."
    return None


def _is_test_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1]
    return (
        normalized.startswith("tests/")
        or "/tests/" in normalized
        or name.startswith("test_")
        or name.endswith("_test.py")
    )


def classify_layered_failure(traj: dict[str, Any]) -> tuple[str, list[str], str]:
    text = _joined_failure_text(traj)
    metadata = traj.get("metadata") or traj.get("task_metadata") or {}
    expected_targets = {
        str(path).strip()
        for path in metadata.get("expected_patch_targets", [])
        if str(path).strip()
    }
    authorized_test_paths = {
        str(path).strip()
        for path in metadata.get("authorized_test_edit_paths", [])
        if str(path).strip()
    }
    authorized_test_paths.update(
        str(path).strip()
        for path in metadata.get("verification_files", [])
        if str(path).strip()
    )
    edited_paths = _edited_paths(traj)
    edited_path_set = set(edited_paths)
    secondary: list[str] = []

    _append_secondary_edit_signals(
        secondary=secondary,
        expected_targets=expected_targets,
        edited_path_set=edited_path_set,
        authorized_test_paths=authorized_test_paths,
    )

    if "verification test_patch apply failed" in text and any(
        signal in text
        for signal in (
            "already exists in working directory",
            "would overwrite",
            "patch does not apply",
        )
    ):
        return (
            "verification_overlay_conflict",
            secondary,
            "External verification could not apply the official regression overlay on top of agent-created files.",
        )
    if _saw_shell_exit_masking_signal(traj):
        if primary_from_secondary := _primary_from_secondary(secondary):
            return primary_from_secondary
        return (
            "shell_exit_masking",
            secondary,
            "A piped verification command reported exit code 0, which can mask upstream pytest failures.",
        )
    if _looks_like_setup_failure(text):
        return "infra_setup_failure", secondary, "Failure text suggests clone, checkout, or baseline setup provisioning problems."
    if _looks_like_dependency_noise(text):
        return "dependency_noise", secondary, "Failure text is dominated by dependency resolution or repeated install attempts."
    if any(signal in text for signal in ("malformed tool-call", "tool '", "toolcallparseerror", "provider", "rate limit", "timeout contacting")):
        return "tool_protocol_or_provider", secondary, "Trajectory shows tool-call protocol or provider-side execution issues."
    if "verification recovery blocked an unlisted test edit" in text:
        return "test_drift", secondary, "Verification recovery attempted an unauthorized test edit."

    if primary_from_secondary := _primary_from_secondary(secondary):
        return primary_from_secondary
    return "genuine_implementation_miss", secondary, "No infra/protocol drift signal dominated; classify as an implementation miss."
