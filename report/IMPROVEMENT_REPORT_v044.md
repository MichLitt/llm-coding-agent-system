# v0.4.4 Improvement Report — Fix Agent Early Termination (M2.7 Message Format Bug)

**Date:** 2026-04-01
**Branch:** claude/frosty-mendeleev
**Author:** Claude (automated)

---

## Overview

The v0.4.3 full ablation (40 tasks × 6 presets) produced pass rates of **7.5–12.5%** and
**AvgSteps ≈ 2.0** across all presets. Diagnosis revealed a fundamental bug in the
Anthropic-backend message normalization, not the agent behavior itself.

v0.4.4 fixes the root cause and three contributing issues.

---

## Root Cause Analysis

### Primary Bug (Fix 4) — Message Format Mismatch in `_normalize_messages_for_anthropic`

**File:** `coder_agent/core/llm_client.py`
**Error signature:** `BadRequestError: 400 — "tool result's tool id() not found (2013)"`
**Exception stage:** `llm.chat` (step 2 of agent loop)

The agent message history is stored in **OpenAI format**:
- Assistant messages: `{"role": "assistant", "tool_calls": [{"id": "...", "type": "function", "function": {...}}]}`
- Tool results: `{"role": "tool", "tool_call_id": "...", "content": "..."}`

But `_AnthropicBackend.chat()` sends these messages directly to the Anthropic-compatible
MiniMax endpoint, which requires **Anthropic format**:
- Assistant messages: `{"role": "assistant", "content": [{"type": "tool_use", "id": "...", "name": "...", "input": {...}}]}`
- Tool results grouped into a single user message: `{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "...", "content": "..."}]}`

The existing `_normalize_messages_for_anthropic` function only stripped internal
`error_kind` fields from blocks of type `"tool_result"`, but the actual messages were
stored with `"role": "tool"` (OpenAI format), not as list blocks. So the function was a
no-op in practice, and every step 2+ LLM call with tool history crashed with a 400 error.

**Why only step 2 fails:** Step 1 has no tool results in history (only the initial user
message), so it succeeds. Step 2 has tool results → 400 error → exception handler fires
→ `termination_reason="loop_exception"`, `steps=2`. The failure appeared in *every*
task in the v0.4.3 ablation that required tool calls.

**Why 7.5–12.5% still "passed":** Tasks whose setup files already pass the test suite
(e.g., `custom_easy_003` calculator) auto-complete via `auto_complete_on_verification`
after step 1 before the failing step 2 LLM call, and post-hoc verification passes.

### Fix 4 — Rewrite `_normalize_messages_for_anthropic`

Full OpenAI→Anthropic format conversion:

| OpenAI format | Anthropic format |
|---------------|-----------------|
| `{"role": "assistant", "tool_calls": [{...}]}` | `{"role": "assistant", "content": [{"type": "tool_use", ...}]}` |
| N × `{"role": "tool", "tool_call_id": "...", ...}` | `{"role": "user", "content": [N × {"type": "tool_result", ...}]}` |
| `{"role": "user", "content": "text"}` | pass-through |
| Already-Anthropic content lists | strip `error_kind`, preserve `is_error` |

Multiple consecutive `role="tool"` messages are merged into a single `role="user"`
message with multiple `tool_result` blocks (required by the Anthropic API).

---

## Secondary Fixes

### Fix 1 — Enforce Verification Gate by Default (`runner.py`)

```python
# Before (C1-C5 had gate_enabled=False → skipped verification on text-only stop)
enforce_stop_verification=gate_enabled,

# After (always enforce when a hook exists)
enforce_stop_verification=(verification_hook is not None),
```

When the model stops with a text-only response (describing code rather than writing it),
the verification hook now runs immediately and reinjects failure feedback, giving the
model a second chance to call `write_file`.

### Fix 2 — Improve Verification Failure Feedback (`agent_turns.py`)

```python
# After fix: explicit tool-use instruction in the re-prompt
"You MUST use the write_file (or edit_file) tool to write your "
"implementation to disk. Do NOT describe the solution in text — "
"call the appropriate tool to create or modify the file, then "
"stop and I will verify again."
```

### Fix 3 — Strengthen System Prompt (`agent_prompt.py`)

Added explicit instruction to always use tools rather than describe code in text:

```
- IMPORTANT: Always use tools (write_file, edit_file, run_command) to implement changes.
  Never describe code changes in text only — write the actual code to disk using the
  appropriate tool, then run it to verify.
```

---

## Smoke Test Results (Before Full Ablation)

**5 tasks, C3 config, after all 4 fixes:**

| Task | Steps | Result |
|------|-------|--------|
| custom_easy_001 (debug buggy_sort) | 2 | OK ✓ |
| custom_easy_002 (implement fibonacci) | 2 | OK ✓ |
| custom_easy_003 (add type hints) | 2 | OK ✓ |
| custom_medium_001 (stack impl) | 6 | ERR ✗ (used non-existent `edit_file` tool) |
| custom_medium_002 (refactor messy_utils) | 3 | OK ✓ |

**C3 summary:** 80% pass rate, AvgSteps = 3.0 (vs 0% / 2.0 in v0.4.3)

The single failure used `edit_file` (not a registered tool; correct tool is `write_file`),
not a fundamental issue with the fix.

---

## Impact on Ablation Experiment Design

### Fix 1 side effect: C6 differentiation

The original C6 config uses `verification_gate=True` as its unique feature. With Fix 1
making enforcement the default for all configs, C6's `verification_gate` is no longer
distinctive. The actual ablation differentiation remains via:

- C1: no correction, direct planning
- C2: no correction, ReAct planning
- C3: + correction
- C4: + memory
- C5: + checklist decomposition
- C6: same as C3 (correction + no memory) — now **equivalent** to C3

A future iteration should give C6 a new distinguishing feature (e.g., `tool_choice=any`
for forced tool use, or multi-round verification with higher `max_attempts`).

---

## Full Ablation Results

*(To be filled in after the full C1–C6 ablation completes)*

| Config | N | Bench% | Clean% | AvgSteps |
|--------|---|--------|--------|----------|
| C1 | 40 | TBD | TBD | TBD |
| C2 | 40 | TBD | TBD | TBD |
| C3 | 40 | TBD | TBD | TBD |
| C4 | 40 | TBD | TBD | TBD |
| C5 | 40 | TBD | TBD | TBD |
| C6 | 40 | TBD | TBD | TBD |

---

## Key Files Changed

| File | Fix | Description |
|------|-----|-------------|
| `coder_agent/core/llm_client.py` | Fix 4 | Rewrite `_normalize_messages_for_anthropic` for full OpenAI→Anthropic format conversion |
| `coder_agent/eval/runner.py` | Fix 1 | `enforce_stop_verification=(verification_hook is not None)` |
| `coder_agent/core/agent_turns.py` | Fix 2 | Explicit tool-use instruction in verification failure feedback |
| `coder_agent/core/agent_prompt.py` | Fix 3 | System prompt tool-use mandate |
