# Improvement Report — v0.8.1-swe-harness

Status: implemented and tested locally; the dev2 evaluation has not run.
Plan: `IMPROVEMENT_PLAN_v0.8.1-swe-harness.md`.

## What changed

### H0: history protocol and context accounting (always on)

- `coder_agent/core/context.py`
  - `validate_tool_call_sequence` raises `HistoryProtocolError` when an assistant's tool calls are not followed, right away, by one result each, or when a tool result has no call before it.
  - `MessageHistory` now estimates each message's size (characters / 3, tool calls included) and keeps `total_tokens` as the estimated size of the whole request.
    - Before, it counted only `usage` passed to `add_message`, which no caller passed. The count stayed at the system prompt's size, so truncation could never start (also noted in `IMPROVEMENT_REPORT_v0.8.0-token-usage.md`).
    - The `usage` argument is removed.
  - `truncate` drops whole turns: an assistant message with its tool results and feedback.
    - It keeps the task (the user messages before the first assistant turn) and the latest turn.
    - It puts the truncation notice after the task.
    - Before, it dropped messages from the front one at a time, so the task went first and a call could lose its results.
  - New helpers: `insert_message`, `recount_tokens`, `cut_observation`.
- `coder_agent/core/agent_loop.py`
  - Validates the history before every model request.
  - Approach-memory injection and removal keep the estimates in step.

### H1: run-scoped switches (off by default)

| Switch | Files | Effect |
|---|---|---|
| `error_signals: semantic` | `agent_tool_batch.py`, `agent_loop.py` | A nonzero exit with no output, or exit 141, does not count as an error. So it adds no retry and triggers no correction hint, edit lock or doom-loop warning. |
| `edit_lock: false` | `agent_turns.py` | Removes the one-file restriction after a failed command. |
| `prompt_profile: swebench` | `agent_prompt.py`, `agent.py` | Repository-repair system prompt. |
| `step_budget_notices: true` | `agent_loop.py` | A user note when 30% of the steps are left (at least 5), and another at 4 left. |
| `finish_check: true` | `agent_loop.py` | When the model stops without an external verifier, at most two reminders, each once per run:<br>• changed files with no command run since → verify first;<br>• nothing changed → make the change or say why not. |
| `max_observation_chars: N` | `context.py` | Tool results over N characters keep their head and tail, with a note on how to narrow them. |
| `max_tool_calls_per_reply: N` | `agent_loop.py` | Calls past N in one reply get an error result without running. |

**About the switches**

- **Validation.** `agent.py` rejects invalid values when the Agent is built (`_validate_runtime_settings`).
- **Message placement.** Every new message is appended at the end of the history.
- **Documentation.** The README lists the switches.

### Evaluation tooling

- `scripts/wp1_dev_harness.sh` does three things:
  - draws dev2: 100 instances, seed 20261030, excluding the pilot and formal samples;
  - runs arms B and H with two seeds each, plus arm R;
  - grades them and writes the summary and the pooled comparison.

## Intended effect

- **Wasted runs.** Fewer runs stopped or misdirected by exits that were not errors.
- **Finished fixes.** More runs that reach and verify a fix within 40 steps, on DeepSeek V4.1 Flash especially.
- **Context.** Bounded per-step context, and a truncation that cannot produce an invalid request.

## Tests

- **New: `tests/test_swe_harness.py`**, 19 tests:
  - each switch on and off;
  - invalid values;
  - every recorded request is a valid tool-call sequence.
- **Rewritten: the token-accounting tests in `tests/test_context_compression.py`**, for the new estimates:
  - whole-turn truncation;
  - the task is kept;
  - the latest turn is kept;
  - sequence validation.
- **Fixed: two fakes in `tests/test_agent_termination.py`.** They returned a tool result id the model never issued, which the validator now rejects.
- **Suite:** 469 passed.

## Rebaseline

**H0**

- It changes `core/context.py` and `core/agent_loop.py` for every preset.
- Behavior differs only in two cases:
  - when a history exceeds the context window (180k estimated tokens), where truncation now actually runs;
  - when a history is invalid, which now fails before the request instead of being sent.
- WP1 requests averaged about 6k (GLM) to 11k (DeepSeek) input tokens; the largest single request is not yet measured (plan section 6).
- The one known invalid history (the doom-loop warning, fixed in `36d6300`) can no longer occur.
- A C3/C4/C6 rebaseline is formally triggered by the rules in `AGENTS.md`. No accepted baseline number is expected to move.

**H1**

- Off by default, so C3 behavior is unchanged.

## Results

To be added after the dev2 runs (plan section 4).
