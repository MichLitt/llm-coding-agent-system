# Evaluation Integrity Audit — v0.8.0

Date: 2026-10-27. Status: findings confirmed in code and git history. No
benchmark semantics were changed by this audit.

## Finding 1 — The stop-time "verification gate" feeds grading tests back to the Agent

`EvalRunner.run_task` builds a verification hook with `build_verification_hook`.
When the Agent tries to stop, the hook runs. On failure, the Agent receives
"verification failed" plus the first error line and keeps working, up to
`max_attempts` (2). On success, the run auto-completes. The hook is the same
check used for final scoring:

| Lane | Hook | Check it runs |
|---|---|---|
| HumanEval | `verify_humaneval` → `run_humaneval_check` | official hidden HumanEval tests (`metadata["test"]`) |
| SWE-bench (local lanes) | `verify_swebench` → `run_swebench_check` | FAIL_TO_PASS / PASS_TO_PASS tests, i.e. the grading tests |
| Custom | `verify_custom` | the task's grading checks |

Affected runs therefore measure **pass@k with a test oracle**, not pass@1, and
they are not comparable with published SWE-bench or HumanEval numbers, which
forbid hidden-test feedback.

**Historical scope.** At `f8def41` (2026-03-11, the v0.4.0 era) the gate was
enabled only for presets with `verification_gate: True`, so only C6 had the
oracle:

- **HumanEval C6 161/164 (98.2%)** was oracle-assisted.
- **HumanEval C3 157/164 (95.7%)** had no gate and is the closer estimate of
  standard single-attempt performance.

The reported "C6 eliminated all clean-stop-but-wrong cases (95.7% → 98.2%)"
is explained by hidden-test feedback, not by a self-verification capability.

## Finding 2 — Since v0.6.0, every preset gets the gate; C3 and C6 are identical

Commit `e55b699` ("Close out v0.6.0 SWE reruns", 2026-04-08) removed
`gate_enabled = …get("verification_gate")` from the runner. The gate is now
enabled whenever a task supplies a hook (`enforce_stop_verification =
verification_hook is not None`), whatever the preset. The `verification_gate`
key in `CONFIG_PRESETS["C6"]` is no longer read anywhere in the runtime.

Consequences:

- **C3 and C6 are behaviorally identical** in every eval run since v0.6.0, and
  both have the oracle from Finding 1. The differences between them measure
  run-to-run noise only. Examples: `custom_v060_cmp_C3` 36/40 vs
  `custom_v060_cmp_C6` 34/40, and SWE promoted v0.7.1 C3 2/8 vs C6 2/8. That
  noise is itself useful: two identical configurations differed by 2/40 tasks.
- **G3** ran both arms with the oracle gate on Custom tasks. Its comparison is
  internally consistent, but its absolute pass rates (76.7% / 83.3%) are
  oracle-assisted. See the parent workspace's G3 erratum for the other G3
  issues.
- The v0.8 ConversationBench and ComplexCodeBench snapshots and the WP2
  Custom-40 utility comparison are oracle-assisted in the same way. The WP2
  comparison is still valid as a relative measure: both arms share the gate.

## Finding 3 — GLM-5.1 spends `max_tokens` on reasoning; the stream cap hid it

GLM-5.1 thinks by default. Its reasoning streams as `delta.reasoning_content`
and counts against `max_tokens` (8192 in `config.yaml`). The client discards
it.

**Probes (2026-10-27/28).**

- `max_tokens` 6000, "write a 250-line module":
  - first chunk after 1.2 s, then a chunk at least every 1.3 s (about
    37 tok/s);
  - all 6000 tokens were reasoning; `content` was empty.
- `max_tokens` 64:
  - `finish_reason` `length`;
  - 63 of 64 tokens were reasoning, and `content` was empty.

**What the WP2 Custom-40 runs showed.** The same 6 tasks failed with
`loop_exception` / `TimeoutError` in both arms (15%):

- `custom_hard_003`
- `custom_hard_005`
- `custom_medium_007`
- `custom_medium_010`
- `custom_medium_011`
- `custom_v8_010`

`_OpenAIBackend.chat` bounded the whole stream at 180 s
(`CODER_LLM_REQUEST_TIMEOUT_SECONDS`, `5887204`). A call that uses its full
8192-token budget needs about 220 s, so it was cut first.

**What lifting the cap revealed.** A rerun of the 6 tasks without the
180 s cap was stopped after 30 minutes on the first task. The first
`custom_hard_003` call returned exactly 8192 tokens of reasoning, with no
text and no tool call. After one `list_dir` call, the next 7 calls did the
same. Each empty reply re-entered the loop through the verification gate,
so every step burned the full budget, about 3.7 minutes each.

The 180 s cap had been turning "the model ran out of budget while thinking"
into a fast, mislabeled timeout.

**Accounting gap.** An aborted call never returned usage and was not counted
as attempted. Token totals therefore undercounted, while
`token_usage_status` still said `provider`.

**Not affected.** The G3 formal runs (`g3_formal-v6_*`) had no such timeouts.
Three appear only in the earlier, non-formal `g3_candidate_seed101` run.

**Fixes (2026-10-28).**

- **Stream bounds.** A stream fails only when:
  - no chunk arrives for `CODER_LLM_STREAM_IDLE_TIMEOUT_SECONDS` (default
    120 s), or
  - it outlives `CODER_LLM_STREAM_TOTAL_TIMEOUT_SECONDS` (default 900 s).

  The error names the bound that fired. `CODER_LLM_REQUEST_TIMEOUT_SECONDS`
  keeps only its HTTP role: connect and per-read timeout.
- **Truncation is reported.**
  - Both backends return `truncated`: OpenAI `finish_reason == "length"`,
    Anthropic `stop_reason == "max_tokens"`.
  - `LLMClient.chat` raises `LLMOutputTruncatedError` when a truncated
    response has no text, no tool call and no parse error. Its usage is
    still recorded.
  - A truncated response that kept a complete tool call is used as before.
- **Aborted calls are counted.** `LLMClient.chat` counts a call that raises
  as attempted with unreported usage, so such runs read `partial`.
- **Tests:**
  - a slow but live stream completes;
  - the idle and total bounds both fire;
  - truncation is detected from a reasoning-only stream;
  - an empty truncated reply raises and keeps its usage, while one with a
    tool call passes;
  - an Agent run stops after one empty truncated reply;
  - a timed-out call reads `partial`.
- **Live check.** `custom_hard_003` with the guard
  (`wp2_truncguard_legacy_s101`):
  - 3 calls in 490 s;
  - stopped with `LLMOutputTruncatedError: LLM output hit max_tokens=8192
    before any text or tool call`;
  - usage `provider`, 16,389 output tokens.

**Budget versus thinking, one task (`custom_hard_003`, C3, seed 101).**

| Setting | Result | Calls | Output tokens | Time |
|---|---|---|---|---|
| `glm_5`, `max_tokens` 8192 | `LLMOutputTruncatedError` | 3 | 16,389 | 490 s |
| `glm_5`, `CODER_MAX_TOKENS=24576` | `LLMOutputTruncatedError` on call 1 | 1 | 24,576 | 633 s |
| `glm_5_nothink` (`thinking` disabled), 8192 | passed | 8 | 4,405 | 101 s |

On this task, a larger budget did not help: the model reasoned through all
24,576 tokens without producing any output.

**Custom-40, thinking on versus off** (C3, `legacy`, seed 101, `max_tokens`
8192). Evidence: `artifacts/glm-thinking/custom40-thinking-on-vs-off-s101.json`.

| Arm | Passed | `loop_exception` | Steps | Output tokens | Input tokens | Wall time |
|---|---|---|---|---|---|---|
| `glm_5` (thinking on) | 32/40 | 6 | 163 | 128,223 | 473,739 | 4,874 s |
| `glm_5_nothink` | 34/40 | 0 | 193 | 69,101 | 602,356 | 2,355 s |

- **Paired.**
  - Thinking on passed 3 tasks the other arm failed: `custom_medium_012`,
    `custom_v8_005`, `custom_v8_008`.
  - Thinking off passed 5 the other arm failed: `custom_hard_001`,
    `custom_medium_005`, `custom_medium_007`, `custom_medium_010`,
    `custom_v8_010`.
  - Exact McNemar p = 0.73. No quality difference is detectable at
    n = 40.
- **The 6 truncated tasks.** With thinking off, 3 passed and 3 failed by
  `retry_exhausted`, an ordinary Agent failure. `custom_hard_003` passed in
  the separate single-task run but failed here, which is run-to-run
  variance.
- **Cost.** Thinking off took more, shorter steps. It used 46% fewer output
  tokens, 27% more input tokens and 52% less wall time.
- **Caveats.**
  - One seed.
  - The oracle gate was on in both arms.
  - The thinking-on arm predates the stream fix. Its 6 failures were
    180 s timeouts, which now end in `LLMOutputTruncatedError`: the same
    outcome.
  - Custom tasks are easier than SWE-bench, where thinking may matter more.

`glm_5_nothink` uses the new optional `extra_body` profile field, which is
merged into every request on both transports.

**Still open: the model setting for WP1.** With thinking on, these tasks
fail honestly and cheaply. Before any GLM benchmark claim (including WP1),
one of these must be chosen:

- **`glm_5_nothink`.** Custom-40 shows no measurable quality loss at half
  the wall time and output tokens, and no runaway reasoning.
- **Thinking on with preserved thinking.** The Coding Plan endpoint enables
  preserved thinking by default, and the provider docs ask clients to
  return `reasoning_content` unmodified in later turns. This client drops
  it, so the thinking-on arm above was not run the way the provider
  intends. Supporting it means storing and replaying reasoning in the
  message history, and it would need its own comparison.

## Finding 4 — Custom-lane tasks can modify the harness environment

Custom-lane commands run with the harness's own `.venv` interpreter on
`PATH`. In the WP2 Custom-40 runs, `custom_medium_012` modified the harness
`.venv` in both arms:

- the `workspace-write` arm ran `ensurepip`, which installed pip;
- about 7 minutes later, the `legacy` arm used that pip to install mypy and
  four dependencies.

Every task that ran afterwards, in either arm, saw a changed environment.

The packages were removed with `uv sync --frozen` after the runs. The WP1
Docker lane is not affected: each instance has its own container.
Recommended fix: a per-task virtualenv for the Custom lane, or a policy rule
that treats installs into an interpreter outside the workspace as writes
outside the workspace.

## What is not affected

- `coder_agent.eval.swebench_docker` (WP1) passes no verification hook. The
  Agent sees only the problem statement, and grading is done afterwards by the
  unmodified SWE-bench harness. It is oracle-free by construction.
- Runtime, service, policy and resume behavior.

## Recommended fix (needs a decision and a rebaseline)

1. **Separate grading from verification.** Add an explicit
   `oracle_feedback` setting, default **off** for every benchmark lane. With
   it off, the stop-time gate may use only signals a user could provide:
   - visible examples (HumanEval prompt doctests, not `metadata["test"]`);
   - the repository's existing tests;
   - an Agent-written reproduction.

   Grading checks run only after the run ends.
2. **Make the preset flag real again.** Restore the `verification_gate`
   preset flag, so C3 and C6 differ only by a non-oracle gate. Keep a
   one-time oracle-on lane only for backwards comparison, labeled as such.
3. **Re-baseline** HumanEval, Custom-40 and the SWE lanes without the oracle
   before any C3/C6 or absolute-score claim.

## Claims guidance

- Do not cite HumanEval 98.2% or any post-v0.6.0 Custom or SWE pass rate as a
  standard pass@1 score.
- Do not cite C6-versus-C3 differences from v0.6.0 onward as a gate effect.
- Citable as an engineering and evaluation-hygiene result:

  > A self-audit found that the evaluation harness fed grading-test results
  > back through the stop-time gate and had silently made two ablation arms
  > identical. Identical-configuration reruns showed ±2/40 run-to-run variance
  > on the Custom-40 suite.

  Once the recommended fix lands and lanes are re-baselined, the claim can
  also say that grading was separated from verification.
