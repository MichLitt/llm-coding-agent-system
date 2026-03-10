import asyncio
import json
import re
from dataclasses import dataclass

from coder_agent.config import cfg


LLM_SYSTEM_PROMPT = """\
You are an expert evaluator analyzing why a coding agent failed a task.
After your analysis, output ONLY a JSON object as your final answer (no markdown, no extra text after the JSON).

Dimension 1 - goal_alignment:
  "correct"  - agent worked toward the right solution but made execution mistakes
  "deviated" - agent misunderstood the task or went in the wrong direction

Dimension 2 - execution_issue (pick ONE):
  "logic"     - code logic is wrong (algorithm, condition, edge case)
  "tool"      - tool call failed (bad args, path, command not found)
  "self_eval" - agent thought it succeeded but solution is actually wrong
  "planning"  - agent lost track of goals, looped, or hit step limit
  "none"      - unclear

End your response with this JSON block:
{"goal_alignment": "...", "execution_issue": "...", "explanation": "one sentence", "fixable_by_more_steps": true_or_false}"""


@dataclass
class LLMTaxonomyResult:
    task_id: str
    goal_alignment: str
    execution_issue: str
    explanation: str
    fixable_by_more_steps: bool
    final_status: str
    steps_used: int


async def classify_one(traj: dict) -> LLMTaxonomyResult:
    from coder_agent.core.llm_client import LLMClient

    steps = traj.get("steps", [])
    step_summaries = []
    for i, step in enumerate(steps):
        thought = (step.get("thought") or "")[:200]
        obs = (step.get("observation") or "")[:300]
        err = step.get("error_type") or ""
        action = step.get("action") or {}
        tool = action.get("tool", "")
        step_summaries.append(
            f"Step {i + 1}: tool={tool or 'none'} err={err or 'none'}\n"
            f"  thought: {thought}\n"
            f"  observation: {obs}"
        )

    user_content = (
        f"Task: {traj.get('task_id', 'unknown')}\n"
        f"Final status: {traj.get('final_status', 'unknown')}\n"
        f"Total steps: {len(steps)}\n\n"
        + "\n".join(step_summaries)
    )

    client = LLMClient()
    result = await client.chat(
        messages=[{"role": "user", "content": user_content}],
        system=LLM_SYSTEM_PROMPT,
        tools=[],
        model=cfg.model.name,
        max_tokens=1024,
        temperature=0.0,
    )

    raw = ""
    for block in result.get("content", []):
        if block.get("type") == "text":
            raw = block["text"].strip()
            break

    parsed = {}
    matches = list(re.finditer(r"\{[^{}]*\}", raw, re.DOTALL))
    for match in reversed(matches):
        try:
            parsed = json.loads(match.group())
            if "goal_alignment" in parsed or "execution_issue" in parsed:
                break
        except json.JSONDecodeError:
            continue

    return LLMTaxonomyResult(
        task_id=traj.get("task_id", "unknown"),
        goal_alignment=parsed.get("goal_alignment", "unknown"),
        execution_issue=parsed.get("execution_issue", "unknown"),
        explanation=parsed.get("explanation", raw[:200]),
        fixable_by_more_steps=bool(parsed.get("fixable_by_more_steps", False)),
        final_status=traj.get("final_status", "unknown"),
        steps_used=len(steps),
    )


def failure_taxonomy_llm(trajs: list[dict]) -> list[LLMTaxonomyResult]:
    failed = [t for t in trajs if t["final_status"] != "success"]
    if not failed:
        return []

    async def run_all():
        results = []
        for traj in failed:
            print(f"  Classifying {traj.get('task_id', '?')}...", flush=True)
            results.append(await classify_one(traj))
        return results

    return asyncio.run(run_all())
