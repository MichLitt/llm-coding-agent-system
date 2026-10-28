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
