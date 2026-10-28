# WP1 Pre-registration (DRAFT) — SWE-bench Verified with official Docker grading

Status: **draft, not frozen.** It is frozen by committing it with the decision
in §6 resolved and every `TBD` filled, before the first formal (non-pilot) run.
Pilot runs may happen before freezing; pilot instances are excluded from the
formal sample.

## 1. Question

How often does this Agent resolve real repository issues under the standard
protocol? That means no hidden-test feedback, official instance images, and
grading by the unmodified SWE-bench harness. How does it compare with a
minimal public agent using the same model and budget?

## 2. Data

- Dataset: SWE-bench Verified, `test` split, 500 instances. The name and
  fingerprint are recorded by `scripts/export_swebench_instances.py`.
- Pilot: 10 instances, seed 20261027, ids recorded in `pilot.meta.json`. Used
  only to validate infrastructure and measure cost.
- Formal sample: 100 instances, seed 20261028, drawn from the 490 non-pilot
  instances with `--exclude pilot-ids.txt`.
- The Agent receives only `problem_statement`. FAIL_TO_PASS and PASS_TO_PASS
  stay in the harness.

## 3. Environment

- Host: WSL2 Ubuntu 24.04, x86_64, 24 CPUs, 31 GB RAM.
- Docker Engine: version recorded at run time.
- Per-instance container from `swebench/sweb.eval.x86_64.<id>:latest`, with
  `--network none`, 2 CPUs, 6 GB memory, `--pids-limit 1024` and
  `no-new-privileges`. Tools run through `docker exec`
  (`coder_agent/tools/container.py`).
- Grading: `python -m swebench.harness.run_evaluation` from the pinned
  `swebench` version (TBD, recorded), with `--predictions_path <arm>/predictions.jsonl`.
- Agent code: the commit recorded in each run's `manifest.json`. Code changes
  between arms are not allowed.

## 4. Arms

| Arm | Configuration | Seeds |
|---|---|---|
| A | This Agent, preset C3, `policy_mode=legacy` (the container is the sandbox), `max_steps=40`, no verification hook | 101, 202 |
| R | mini-SWE-agent (version TBD, pinned), same model profile, step limit 40, default prompt | 1 run |
| B (only if §6 option 2 is chosen) | A plus a non-oracle self-verification gate, frozen before the formal run | 101, 202 |

Model: profile `glm_5` (`glm-5.1`), with temperature and `max_tokens` from
`config.yaml` at the pinned commit.

## 5. Measures and analysis

- **Primary.** Resolved rate of arm A on the 100 formal instances, reported
  per seed and as "resolved in both seeds". Each comes with a 95% Wilson
  interval.
- **Secondary.**
  - A versus R: per-instance paired comparison, exact McNemar test using
    seed 101 against R.
  - Cost per instance from provider-reported tokens (WP0).
  - Wall time; steps; termination reasons; patch-apply failures.
- **Infrastructure failures** (image pull, container start, harness error):
  - classified separately and retried once with identical settings;
  - never counted as Agent failures, never silently dropped;
  - reported with ids.
- **No early stopping** on results. All instances in all arms run, and
  negative results are reported.
- With n = 100, only differences of roughly 10–15 percentage points are
  detectable. A null A–R or A–B difference will be reported as such.

## 6. Open decision: the verification gate

The plan's original primary hypothesis was "verification gate on (C6) versus
off (C3)". `EVAL_INTEGRITY_AUDIT_v0.8.0.md` shows that the existing gate uses
the grading tests (an oracle), and that since v0.6.0 C3 and C6 are identical.
An oracle gate is not allowed on Verified. Choose one:

1. **Drop the gate arm.** The primary result is A's absolute resolved rate plus
   the external reference R. This is the recommended choice: cheapest, and
   valid now.
2. **Implement a non-oracle gate first, then add arm B.** At stop time the
   gate would run the repository's existing tests for the files the Agent
   changed, plus the Agent's reproduction script. This is a new feature and
   must be frozen with its own tests before the formal run.

## 7. Budget and stopping

- **Pilot first.** It measures tokens and cost per instance (WP0 accounting).
  The formal run starts only after a hard spend cap is recorded here (TBD).
- **Per-instance wall cap:** 30 minutes. A run that exceeds it is a budget
  failure for that instance, reported in its own category.

## 8. Outputs

For each arm:

- `manifest.json`, `predictions.jsonl`, `results.jsonl`;
- the harness report JSON and harness logs;
- an EvalOps `agent/v1` aggregate report.

A versioned report gives results by seed and arm, the failure taxonomy, cost,
and every deviation from this pre-registration.
