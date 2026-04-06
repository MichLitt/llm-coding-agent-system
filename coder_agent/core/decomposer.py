"""Decomposer: generates an Adaptive Checklist for a task and tracks sub-goal progress.

The Decomposer is the first node in the C5 LLM System architecture:
  Decomposer → Actor (ReAct loop) → Critic (eval)

It makes one LLM call at the start of a task to produce an ordered list of sub-goals,
then maintains their completion state as the Agent executes steps.  At each step, the
Actor receives a progress prompt injected into its context so it always knows what
remains to be done.
"""

from __future__ import annotations

import json
import re
from typing import Any


_DECOMPOSE_SYSTEM = """\
You are a task decomposition specialist for a coding agent.
Break the given programming task into 3-6 ordered, atomic sub-goals.

Each sub-goal must be:
- Independently verifiable (has a clear completion criterion)
- Appropriately scoped (not too fine-grained, not too broad)
- Ordered by dependency

Respond with ONLY a JSON array of strings (no markdown, no extra text):
["sub-goal 1", "sub-goal 2", ...]"""


class Decomposer:
    """Generates and tracks an Adaptive Checklist of sub-goals for a task."""

    def __init__(self) -> None:
        self._goals: list[str] = []
        self._done: list[bool] = []

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def decompose(self, task: str, client: Any) -> list[str]:
        """Call LLM once to generate the sub-goal checklist for *task*.

        Args:
            task: The full task description.
            client: An LLMClient instance.

        Returns:
            Ordered list of sub-goal strings.
        """
        result = await client.chat(
            messages=[{"role": "user", "content": task}],
            system=_DECOMPOSE_SYSTEM,
            tools=[],
            model=client.profile.model,
            max_tokens=512,
            temperature=0.0,
        )

        raw = ""
        for block in result.get("content", []):
            if block.get("type") == "text":
                raw = block["text"].strip()
                break

        goals = self._parse_goals(raw)
        self._goals = goals
        self._done = [False] * len(goals)
        return goals

    def update(self, steps: list[dict]) -> None:
        """Heuristically mark sub-goals as completed based on step observations.

        A sub-goal is considered done when a recent step has a successful
        exit code (Exit code: 0) and the observation contains keywords
        from the sub-goal text.
        """
        if not self._goals or not steps:
            return

        recent_obs = " ".join(
            (s.get("observation") or "") for s in steps[-3:]
        ).lower()
        recent_success = any(
            "exit code: 0" in (s.get("observation") or "").lower()
            for s in steps[-3:]
        )

        for i, (goal, done) in enumerate(zip(self._goals, self._done)):
            if done:
                continue
            # Simple keyword heuristic: check if significant words from the
            # goal appear in recent observations alongside a success signal.
            keywords = [
                w for w in re.findall(r"\w+", goal.lower())
                if len(w) > 3 and w not in {"that", "this", "with", "from", "have", "will", "make"}
            ]
            if keywords and recent_success:
                matched = sum(1 for kw in keywords if kw in recent_obs)
                if matched >= max(1, len(keywords) // 2):
                    self._done[i] = True

    def to_progress_prompt(self) -> str:
        """Return a concise progress string to inject into the Actor's context."""
        if not self._goals:
            return ""

        lines = ["[Task Progress]"]
        for i, (goal, done) in enumerate(zip(self._goals, self._done), start=1):
            marker = "✅" if done else "⏳"
            lines.append(f"  {marker} [{i}] {goal}")

        remaining = [g for g, d in zip(self._goals, self._done) if not d]
        if remaining:
            lines.append(f"\nNext: focus on — {remaining[0]}")
        else:
            lines.append("\nAll sub-goals completed. Provide a final summary and stop.")

        return "\n".join(lines)

    @property
    def all_completed(self) -> bool:
        return bool(self._done) and all(self._done)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_goals(self, raw: str) -> list[str]:
        """Extract a JSON list of strings from the LLM output."""
        # Strip <think>...</think> blocks first
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

        # Find last JSON array in the output
        matches = list(re.finditer(r"\[.*?\]", raw, re.DOTALL))
        for m in reversed(matches):
            try:
                parsed = json.loads(m.group())
                if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
                    return [g.strip() for g in parsed if g.strip()]
            except json.JSONDecodeError:
                continue

        # Fallback: split numbered lines
        lines = []
        for line in raw.splitlines():
            line = re.sub(r"^\s*[\d\-\*\.]+\s*", "", line).strip()
            if line:
                lines.append(line)
        return lines[:6] if lines else ["Complete the task"]
