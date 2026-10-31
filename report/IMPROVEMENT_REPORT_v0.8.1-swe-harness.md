# Improvement Report — v0.8.1-swe-harness

Status: implemented and tested; dev2 evaluated on DeepSeek V4.1 Flash (2026-10-31): no improvement.
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
| `correction_hints: false` (ablation round) | `agent_tool_batch.py` | No correction hint or "Previous command failed" feedback after a failure; the run goes on. |
| `retry_policy: off` (ablation round) | `config.py`, `agent_loop.py` | Failures never end the run. |
| `doom_loop_threshold: 0` | `agent_loop.py` (existing) | No doom-loop warning; the value is now validated. |

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
- In WP1 the median request was 5–9k tokens and the largest 72k (plan section 6), far below the 180k window.
- The one known invalid history (the doom-loop warning, fixed in `36d6300`) can no longer occur.
- A C3/C4/C6 rebaseline is formally triggered by the rules in `AGENTS.md`. No accepted baseline number is expected to move.

**H1**

- Off by default, so C3 behavior is unchanged.

## Results (dev2, DeepSeek V4.1 Flash)

Exploratory, not pre-registered. Run on the WSL host with
`scripts/wp1_dev_harness.sh`, at agent `8fcd364`.

**Evidence** (`artifacts/wp1-dev2/`)

- `dev2-sample.json`: the 100 ids and the host files' hashes. The exclusion list rebuilt from the formal meta matches the host's hash.
- `dev2-outcomes.json`: per-instance outcomes, usage and termination of the five arms.
  - They were transcribed from the host's `summary.json` (sha256 `eb6a18a6…`).
  - They reproduce all ten pair tables the host printed.
- `dev2-pooled.json`: the pooled comparison, recomputed from the outcomes. It matches the host's `pooled.json` (`334c6998…`).

### Resolved

| Arm | Seed 101 | Seed 202 | Mean |
|---|---|---|---|
| B (C3 + consecutive) | 50 | 53 | 51.5 |
| H (B + every v0.8.1 switch) | 51 | 49 | 50.0 |
| R (mini-SWE-agent) | 65 | | 65 |

**H against B, pooled per instance.** 100 against 103 of 200 runs: −1.5 pp (95% bootstrap −8.5 to +6.0), exact sign-flip p = 0.79.

- **Score differences, H − B, by instances:**
  - better: +2 on 5, +1 on 8;
  - equal: 69;
  - worse: −1 on 15, −2 on 3.
- **H on the instances B resolved in both seeds (43):**
  - it resolved 73 of 86 runs, so it lost 13 runs B had;
  - on the 40 B never resolved, it resolved 12 of 80.

### Where the runs went

Termination over both seeds. In parentheses: how many resolved.

| | B | H |
|---|---|---|
| model_stop | 115 (87) | 134 (90) |
| max_steps | 74 (12) | 56 (5) |
| retry_exhausted | 11 (4) | 10 (5) |

- **Fewer runs hit the cap.** H turned 18 capped runs into runs that stopped on their own.
- **But the extra stops were mostly wrong.**
  - Model stops with a wrong patch rose from 28 to 43.
  - Capped runs that still resolved fell from 12 to 5: these runs keep editing to the end and leave a correct diff.
- **The step-budget notices and the finish check push a model to stop.** On DeepSeek that trades a capped run for a premature, unverified-enough fix, which costs instances that B resolves reliably. Section 6 of the plan had already found that nearly every run verifies after its last edit, so the finish check could not add much.

### Cost

| | B | H | R |
|---|---|---|---|
| Input, both seeds | 47.3M | 42.4M (−10%) | 24.2M (one run) |
| Output | 752k | 638k (−15%) | 440k |
| LLM calls | 4,481 | 4,508 | 2,203 |

At DeepSeek's peak list prices dev2 cost about ¥29, R included.

### Against mini-SWE-agent

- **The gap.** R resolved 65, against 50–53 for every one of our runs (McNemar p = 0.002–0.03 per pair). The gap matches the one on the formal sample (64 against 54.5).
- **Capability is not the limit; reliability is.** Our four runs together resolved 67 instances and R resolved 65; only 9 instances were R's alone. Our per-run rate is the problem.
- **Its capped runs are all empty patches.** R hit its 40-call limit 20 times, every time with an empty patch.

## Decision

Per plan section 4.3, a result below zero calls for ablation, not a GLM recheck or a held-out run.

- **The data point to two sources.**
  - The stopping nudges (`step_budget_notices`, `finish_check`): extra wrong stops and lost easy instances.
  - The distance to mini-SWE-agent in how the loop treats failures. C3's correction hints fire on about 10% of steps, most after expected failures. They cannot simply be turned off: the C2 preset ends a run at its first nonzero exit.
- **Rebaseline.** No accepted number changes, and H stays off by default.
