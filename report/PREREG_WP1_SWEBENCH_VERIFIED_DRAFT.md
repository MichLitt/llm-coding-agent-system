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
- Grading: `python -m swebench.harness.run_evaluation` from `swebench`
  5.0.2 (Python 3.12.3 venv on the WSL host), with
  `--predictions_path <arm>/predictions.jsonl`.
- Harness validation (2026-10-28): a gold-patch run on 5 Verified instances
  sampled with the pilot seed 20261027 resolved 5/5, with 0 errors and 0
  unstopped containers. The instances were `django__django-10914`, `-11951`,
  `-12276`, `-13109` and `sphinx-doc__sphinx-9230`. The prebuilt images
  pulled and graded in about 2.5 minutes in total.
- Linux validation of the Agent side (2026-10-28, run by the user on the
  WSL host at `b2bbf22`; outputs as expected):
  - `uv sync --frozen && uv run pytest -q` passed;
  - a `DockerSandbox` smoke on the `django__django-10914` image resolved
    `python` to the image's conda `testbed` env;
  - it confirmed no network inside the container;
  - a newly written file appeared in `diff()`.
- Agent code: the commit recorded in each run's `manifest.json`. Code changes
  between arms are not allowed.
- **Procedure.** `scripts/wp1_pilot.sh` runs the pilot end to end:
  1. export the sample;
  2. run arm A (`coder_agent.eval.swebench_docker`);
  3. run arm R (`scripts/wp1_reference_arm.sh`);
  4. grade both with the harness;
  5. summarize with `coder_agent.eval.swebench_summary`.

  The summary recomputes every reported number from the run directories
  and records each arm's commit, model settings, instance-file hash,
  harness-report hash and image ids.

## 4. Arms

| Arm | Configuration | Seeds |
|---|---|---|
| A | This Agent, preset C3, `policy_mode=legacy` (the container is the sandbox), `max_steps=40`, no verification hook | 101, 202 |
| R | mini-SWE-agent 2.4.6 (litellm 1.93.0), its default SWE-bench prompt, same model and request settings (including `thinking` disabled), step limit 40, `--network none` | 1 run |
| B (only if §6 option 2 is chosen) | A plus a non-oracle self-verification gate, frozen before the formal run | 101, 202 |

**Model (decided 2026-10-28).**

- Profile `glm_5_nothink`: `glm-5.1` on the Coding Plan endpoint, with
  `thinking: {"type": "disabled"}` sent in every request.
- Temperature 0.2 and `max_tokens` 8192, from `config.yaml` at the pinned
  commit. The runner records them in `manifest.json` under
  `model_settings`, together with the profile's `extra_body`, and refuses
  to resume a run whose settings changed.
- `CODER_MAX_TOKENS` must be unset.

Why thinking is disabled (`EVAL_INTEGRITY_AUDIT_v0.8.0.md`, Finding 3):

- With thinking on, GLM-5.1's reasoning counts against `max_tokens`. On some
  tasks it used the whole budget, at 8192 and at 24,576, with no text or
  tool call.
- On Custom-40 (seed 101, oracle gate on in both arms), thinking off scored
  34/40 against 32/40 for thinking on (McNemar p = 0.73). It had no
  truncations, 46% fewer output tokens and 52% less wall time.

The pilot uses the same profile. Thinking on with preserved reasoning is
not tested in WP1; a result here says nothing about it.

Arm R sends the same `thinking` field through litellm's `extra_body`.

- **Config.** `scripts/wp1_mini_swe_agent.yaml` is layered on
  mini-SWE-agent's built-in `swebench.yaml`, and the API base comes from
  the `glm_5_nothink` profile.
- **Preflight** (`scripts/wp1_reference_preflight.py`):
  - checks the merged config: step limit 40, no network, thinking
    disabled, `max_tokens` 8192;
  - sends one 64-token request through mini-SWE-agent's own model class,
    which must not stop at the length limit while reasoning. Measured on
    2026-10-28: `finish_reason=stop`, 0 reasoning tokens.
- **Archived with the run:** both config files and the venv's package
  list.
- **Why litellm is pinned.** mini-SWE-agent sets no upper bound on it, and
  the newest release at the time (1.106.5) failed on import without the
  optional `mcp` package.

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
- **Per-instance wall cap:** 30 minutes of Agent time, from the first
  model call. A run that reaches it is stopped at the next step boundary and
  counted as a budget failure (`wall_cap`). It is graded as an empty patch,
  and its partial patch is kept under `discarded_patches/`.
- **Retries.** Infrastructure failures (runner exceptions) and provider
  failures (connection, rate-limit, server and idle-stream errors) are
  retried once with `--retry-errors`. Every attempt stays in
  `results.jsonl`, and only the last prediction per instance is graded.

## 8. Outputs

For each arm:

- `manifest.json`, `predictions.jsonl`, `results.jsonl`;
- the harness report JSON and harness logs;
- an EvalOps `agent/v1` aggregate report.

A versioned report gives results by seed and arm, the failure taxonomy, cost,
and every deviation from this pre-registration.
