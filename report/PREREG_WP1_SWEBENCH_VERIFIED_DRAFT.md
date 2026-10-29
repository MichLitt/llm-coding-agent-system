# WP1 Pre-registration — SWE-bench Verified with official Docker grading

Status: **frozen** (2026-10-28).

- **The freeze commit** is the one that set this line; it is listed by
  `git log -- report/PREREG_WP1_SWEBENCH_VERIFIED_DRAFT.md`. The file
  keeps its draft-era name so existing links stay valid.
- **Later changes are amendments.** Each is dated, gives its reason, is
  recorded under "Amendments" at the end, and is committed before the
  runs it affects.
- `scripts/wp1_formal.sh` refuses to start unless this line says frozen.
- Pilot instances are excluded from the formal sample.

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
  `--network none`, 2 CPUs, 6 GB memory, `--pids-limit 1024`,
  `no-new-privileges` and a UTF-8 locale (`LANG`/`LC_ALL=C.UTF-8`, added
  after the pilot). Tools run through `docker exec`
  (`coder_agent/tools/container.py`).
- Changes after the pilot (`WP1_PILOT_2026-10-28.md`), all made before
  freezing:
  - no-op edits are rejected;
  - missing tool arguments are named;
  - the UTF-8 locale above;
  - in-run retries and a shared run-state store.

  The retry guard is unchanged (decided 2026-10-28; see §5).
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
- **Procedure.** `scripts/wp1_formal.sh` runs the formal run. It refuses to
  start until this file is frozen: committed, marked frozen, with no open
  placeholders left. In order, it:
  1. exports the 100-instance sample (seed 20261028, excluding the pilot
     ids);
  2. runs A-s101 up to the §7 budget checkpoint, then the rest of A-s101,
     then A-s202 and R;
  3. grades each arm;
  4. summarizes.

  `scripts/wp1_pilot.sh` runs the pilot end to end:
  1. export the sample;
  2. run arm A (`coder_agent.eval.swebench_docker`);
  3. run arm R (`scripts/wp1_reference_arm.sh`);
  4. grade both with the harness;
  5. summarize with `coder_agent.eval.swebench_summary`.

  The summary recomputes every reported number from the run directories
  and records each arm's commit, model settings, instance-file hash,
  harness-report hash and image ids.

  Before any container starts, the script sends one small request through
  each arm's own client. The endpoint must be the Coding Plan endpoint
  `https://api.z.ai/api/coding/paas/v4`. The same key on the general
  endpoint fails with 429 code 1113. The runner records the endpoint in
  `model_settings.base_url`.

## 4. Arms

| Arm | Configuration | Seeds |
|---|---|---|
| A | This Agent, preset C3, `policy_mode=legacy` (the container is the sandbox), `max_steps=40`, no verification hook | 101, 202 |
| R | mini-SWE-agent 2.4.6 (litellm 1.93.0), its default SWE-bench prompt, same model and request settings (including `thinking` disabled), step limit 40, `--network none` | 1 run |

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
  - Termination reason against outcome, per arm (`termination_by_outcome`
    in the summary). The aim is to show how many runs the Agent's retry
    guard stopped, and how many of those resolved anyway.
    - The guard (`agent.max_retries` = 3) ends a run at its fourth error
      step. It counts cumulatively, not as a streak. It stays in arm A
      unchanged.
    - In the pilot, 10 of 19 arm-A runs ended this way. 5 of them were
      unresolved, and arm R resolved 2 of those 5.
    - Any cost of the guard is reported, not tuned away.
- **Infrastructure failures** (image pull, container start, harness error):
  - classified separately and retried once with identical settings;
  - never counted as Agent failures, never silently dropped;
  - reported with ids.
- **No early stopping** on results. All instances in all arms run, and
  negative results are reported.
- With n = 100, only differences of roughly 10–15 percentage points are
  detectable. A null A–R difference will be reported as such.

## 6. Decision: no verification-gate arm (decided 2026-10-28)

The plan's original primary hypothesis was "verification gate on (C6) versus
off (C3)". `EVAL_INTEGRITY_AUDIT_v0.8.0.md` shows two problems with it:

- the existing gate uses the grading tests, which is an oracle and not
  allowed on Verified;
- since v0.6.0, C3 and C6 are identical.

**Chosen option: drop the gate arm.** The primary result is arm A's
absolute resolved rate, with arm R as the external reference.

- There is no arm B.
- WP1 makes no claim about verification gates.
- Arm A runs without any verification hook, as §4 states.

The rejected alternative was to implement a non-oracle gate first and add
an arm B. That gate would run the repository's existing tests for the
changed files plus the Agent's reproduction script. It was rejected because
it is a new feature: it would have to be built, tested and piloted before
freezing.

## 7. Budget and stopping

- **Pilot first.** It measures tokens and cost per instance (WP0
  accounting). Results are in `WP1_PILOT_2026-10-28.md`.
- **Spend cap (decided 2026-10-28).** Pricing is not configured and the GLM
  Coding Plan is a subscription, so the cap is stated in provider-reported
  tokens.
  - **Order.** Arms run in the order A-s101, A-s202, R. Arm A processes the
    formal sample in a seeded random order (order seed 20261029, frozen in
    each manifest), so any prefix is a fair subsample. The order does not
    change which instances run or how they are graded.
  - **Checkpoint.** After the first 25 instances of A-s101, whole-run usage
    is projected as the mean usage per measured instance × 300 instance
    runs (3 arms × 100). Instances without usage, such as infrastructure
    failures, are left out of the mean. mini-SWE-agent does not record
    tokens, so the projection assumes arm R uses as much as arm A.
  - **Cap.** If projected input exceeds 45,000,000 tokens, or projected
    output exceeds 2,000,000 tokens, the run stops. The output cap was
    1,000,000 at freezing; Amendment 1 raised it.
    - Continuing needs a dated amendment to this file, stating the new cap
      and the reason, committed before any further instance runs.
    - Finished instances are kept and reported.
  - **Basis.** The pilot used about 44k–73k input and 1.6k output tokens
    per arm-A instance. That projects about 20M input and 0.5M output for
    the whole run, so the cap leaves at least a 2× margin; the pilot sample
    was 80% django.
  - **Enforcement.** `scripts/wp1_formal.sh` runs the checkpoint with
    `coder_agent.eval.swebench_budget`. A non-zero exit stops the script,
    and the projection is kept as `budget-checkpoint.json`.
- **Per-instance wall cap:** 30 minutes of Agent time, from the first
  model call. A run that reaches it is stopped at the next step boundary and
  counted as a budget failure (`wall_cap`). It is graded as an empty patch,
  and its partial patch is kept under `discarded_patches/`.
- **Retries.** Infrastructure failures (runner exceptions) and provider
  failures (connection, rate-limit, server and idle-stream errors) are
  retried once with `--retry-errors`. Every attempt stays in
  `results.jsonl`, and only the last prediction per instance is graded.
  Arm R's provider failures are retried the same way by
  `scripts/wp1_reference_retry.sh` (Amendment 2).

## 8. Outputs

For each arm:

- `manifest.json`, `predictions.jsonl`, `results.jsonl`;
- the harness report JSON and harness logs;
- an EvalOps `agent/v1` aggregate report.

A versioned report gives results by seed and arm, the failure taxonomy, cost,
and every deviation from this pre-registration.

## Amendments

### Amendment 1 (2026-10-28): output cap raised to 2M tokens

**What changed.**

- §7 output cap: 1,000,000 → 2,000,000 tokens. The input cap stays at
  45,000,000.
- `--stop-after N` now runs only the first N instances of the processing
  order (commit `4238fba`). It used to run the next N unfinished
  instances, so rerunning `scripts/wp1_formal.sh` after the stop would
  have run instances 26–50 before any new check.

**Why.** The checkpoint stopped the run as designed. After the first 25
instances of A-s101 (agent `ad4964e`):

| | Mean per instance | Projected, 300 runs | Cap at freezing |
|---|---|---|---|
| Input tokens | 75,345 | 22,603,560 | 45,000,000 |
| Output tokens | 3,520 | 1,055,916 | 1,000,000 |

- Only output exceeded its cap, by 5.6%. Input is at half of its cap.
- Output per instance was about 2.2× the pilot's (about 1,600), and input
  was at the top of the pilot's range. The pilot report warned that its
  sample, 80% django, could understate cost.
- The new cap leaves about 1.9× margin over the measured projection, close
  to the 2× margin the original cap left over the pilot projection.
- The decision used only the projection above. No formal instance had been
  graded (the script grades an arm only after it finishes).

**Effect on the run.**

- The 25 finished instances are kept. Rerunning `scripts/wp1_formal.sh`
  repeats the checkpoint on the same 25 instances against the new cap,
  without running any instance, and then continues.
- Code changed after `ad4964e`: the runner's `--stop-after` and its test,
  the cap in `scripts/wp1_formal.sh`, and this file. The Agent loop,
  tools, prompts and `config.yaml` are unchanged.
- The runner rewrites `agent_commit` in `manifest.json` when it resumes,
  so the A-s101 manifest will name this amendment's commit. Its first 25
  instances ran at `ad4964e`.

### Amendment 2 (2026-10-28): arm R's provider failures retried once

**What changed.**

- Arm R's provider failures are retried once with identical settings,
  as §5 and §7 require for infrastructure and provider failures. Arm A's
  runner already did this; arm R's script had no retry step.
- `scripts/wp1_reference_retry.sh` does the retry:
  - it selects the instances whose first attempt ended with a provider
    exit status (`RateLimitError` and similar), using that status alone;
  - it reruns them into `R/retry-1/` with the first attempt's archived
    configs. It refuses to start if the mini-SWE-agent venv differs from
    the archived `requirements.lock`;
  - it regrades arm R from each instance's last attempt and rebuilds the
    summary.
- The first attempt's trajectories are kept, and so are its predictions,
  harness report and summary (as `*.attempt1.*`).
- A provider failure in the retry is final and is reported as a provider
  error.
- The summarizer now reads arm R's trajectories: exit status, attempts,
  steps and provider-reported tokens. A provider exit status is a
  provider error, never an Agent failure. §7 said mini-SWE-agent does not
  record tokens; its trajectories do keep each call's usage, so arm R's
  tokens are now reported.

**Why.** Arm R ran last.

- From 18:45 to 19:32 EDT on 2026-10-28, 16 of its 100 instances ended
  with `RateLimitError`: z.ai 429 code 1308, "Usage limit reached for 5
  hour", the Coding Plan quota, after both arm-A runs.
  - 14 failed on their first model call.
  - Two failed partway: `matplotlib-23299` after 20 calls and
    `matplotlib-20826` after 28.
  - Calls worked again afterwards: 28 instances were submitted after
    19:32.
- The harness graded all 16 as empty patches, so arm R's first attempt
  scored 61/100 with them counted as failures.
- The summary at `1fb16fa` showed 0 provider errors for arm R, because it
  read only the harness report.
- Arm A had no provider failures in either seed.
- This decision was made after all three arms were graded. It leaves no
  choice that the results could steer: the selection is mechanical, and
  §5 and §7 already required the retry.

**Effect on the run.**

- Arm A is unchanged.
- Arm R is reported from each instance's last attempt. The first-attempt
  result (61/100, with 16 provider failures) is reported alongside it.
- The retry runs at a different time from the first attempt, with the
  same model, endpoint, settings and package versions.
- Code changed after `1fb16fa`:
  - `coder_agent/eval/mini_swe_agent.py` (new) and the summarizer;
  - `scripts/wp1_reference_retry.sh` (new);
  - `scripts/wp1_reference_arm.sh`, whose predictions are now built by the
    same module, with identical output when there is no retry;
  - their tests and this file.

  The Agent loop, tools, prompts and `config.yaml` are unchanged.
