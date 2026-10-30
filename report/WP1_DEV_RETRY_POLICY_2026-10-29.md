# WP1 Development Check — Consecutive Retry Policy (2026-10-29)

Exploratory and not pre-registered. It reuses the WP1 formal sample and
arms (`WP1_RESULTS_2026-10-28.md`), and it decides only whether a held-out
confirmatory run is worth its cost.

**In short.**

- The candidate changes one thing: the retry guard counts only
  consecutive error steps (`agent.retry_policy: consecutive`). The default
  stays `cumulative`.
- On the 100 formal instances, the candidate resolved 71% and 68% across
  its two seeds. The baseline resolved 64% and 59%.
- Pooled per instance over both seeds, that is 139 of 200 runs against
  123: +8.0 pp, 95% bootstrap interval +1.5 to +14.5, exact sign-flip
  p = 0.028.
- That is mini-SWE-agent's level (70%) on the same model, with about a
  quarter less input.
- It costs more than the baseline:
  - input ×1.49, of which 93% is cached (uncached input ×1.25);
  - output ×1.10;
  - LLM calls ×1.18.
- On DeepSeek V4.1 Flash the gain was smaller and not significant: 115
  of 200 runs against 109, +3.0 pp (95% −2 to +8, p = 0.35). There, most
  runs the guard no longer stopped ran out of steps instead.
- mini-SWE-agent on DeepSeek resolved 64%. On that model both of our
  configurations stay below it: the baseline averages 54.5% and the
  candidate 57.5%. On glm-5.1 the candidate matched it.
- The DeepSeek runs exposed a bug that GLM had tolerated: the doom-loop
  warning went between an assistant's tool calls and their results.
  DeepSeek rejects that history. It is fixed (`36d6300`); the GLM arms here
  ran with it in both arms.
- The change was designed from this sample's failures, so the estimate
  is likely optimistic. It justifies a held-out run; it is not a result to
  claim.

## Why this change

The WP1 formal report's exploratory analyses traced the baseline's losses
to its retry guard:

- **How often it stopped runs.** It stopped 40 and 50 of the baseline's
  100 runs, at a median of step 13–14.
- **What it counted.** It counts every step with a nonzero exit or a tool
  error. Most of those were expected: failing tests, reproduction scripts,
  searches with no match. Only 11 of 90 stops followed four error steps in
  a row.
- **What it cost.** Of the instances only mini-SWE-agent resolved, 11 of
  12 (against seed 101) and 16 of 19 (against seed 202) were runs the
  guard had stopped.

The candidate (`ed2fcbd`) changes only the counting. A step with no
nonzero exit and no tool error resets the count, so a run stops after
four consecutive error steps (`max_retries` = 3), not four in total.

## Setup

Arm C is arm A of the formal run with `retry_policy: consecutive`. Every
other setting is the same:

- C3 preset, profile `glm_5_nothink` (glm-5.1, thinking off, GLM Coding
  Plan endpoint), legacy policy mode;
- 40 steps, a 30-minute wall cap per instance;
- the same instances in the same processing order (order seed 20261029);
- seeds 101 and 202, 2 workers;
- the WSL host and the unmodified `swebench` 5.0.2 harness.

The script is `scripts/wp1_dev_retry_policy.sh`.

**Provider failures.** The GLM quota ran out in both seeds: 46 instances
of C-s101 and 11 of C-s202 failed, each the tail of the processing order.
The runner's default of two attempts was already used up, so
`--max-attempts` (`7c8cf2d`) let the script rerun them after the quota
reset. Each seed's predictions were then regraded in full. The final
summary has no infrastructure, provider or harness failures in any arm.

## Evidence

| File | Contents |
|---|---|
| `artifacts/wp1-dev/stage1-outcomes.json` | per-instance outcomes of the five arms, token usage, C's termination by outcome |
| `artifacts/wp1-dev/stage1-pooled.json` | the pooled comparison below |

- **The formal arms** come from the archived, hash-verified
  `artifacts/wp1-formal/summary.json`.
- **The C arms** were printed on the host from
  `runs/wp1/dev-consecutive/summary.json`, as resolved or not per instance.
  - They are not hash-verified, unlike the formal evidence.
  - The formal arms' usage printed by the same command matches the
    archive exactly, which checks the transcription.
  - The per-instance outcomes reproduce every pair table the host
    printed.
- **Run directories** stay on the host under `runs/wp1/dev-consecutive/`.

To recompute the pooled comparison:

```bash
uv run python -m coder_agent.eval.swebench_pooled --summary artifacts/wp1-dev/stage1-outcomes.json --baseline A-s101,A-s202 --candidate C-s101,C-s202
```

## Results

### Per seed

Rate differences are candidate minus baseline, with the summarizer's paired
bootstrap interval.

| Seed | Baseline A | Candidate C | Only A | Only C | Exact McNemar p | Difference (95%) |
|---|---|---|---|---|---|---|
| 101 | 64 | 71 | 8 | 15 | 0.21 | +7 pp (−2 to +16) |
| 202 | 59 | 68 | 4 | 13 | 0.049 | +9 pp (+1 to +17) |

**Against mini-SWE-agent (R, 70).** C-s101 resolved 71 (R only 8, C only
9, p = 1.0). C-s202 resolved 68 (8 and 6, p = 0.79).

**Seed variance.** The seeds of one configuration differ by about as much
as the effect: A-s101 against A-s202 is +5 pp (p = 0.36), and C-s101
against C-s202 is +3 pp (p = 0.58). One seed per arm cannot measure an
effect of this size on 100 instances.

### Pooled per instance

Each instance scores 0–2 per configuration: the number of its two seeds
that resolved it. Instances, not runs, are the independent units.

| | Value |
|---|---|
| Resolved runs, A / C | 123 / 139 of 200 |
| Difference | +8.0 pp (95% bootstrap +1.5 to +14.5) |
| Exact sign-flip p (two-sided) | 0.028 |
| Score difference C − A, by instances | +2: 4, +1: 17, 0: 73, −1: 3, −2: 3 |
| Resolved in both seeds, A / C | 52 / 63 |

Where the candidate's resolves came from, by how often the baseline
resolved each instance:

| Baseline resolved | Instances | Baseline runs resolved | Candidate runs resolved |
|---|---|---|---|
| 0 of 2 | 29 | 0 of 58 | 13 of 58 |
| 1 of 2 | 19 | 19 of 38 | 30 of 38 |
| 2 of 2 | 52 | 104 of 104 | 96 of 104 |

The gain sits mostly on instances the baseline resolved in only one seed,
the runs the guard used to cut short. These groups are defined by the
baseline's own results, so the contrast within them is descriptive.

### Termination

| Arm | model_stop | retry_exhausted | max_steps | loop_exception | Resolved |
|---|---|---|---|---|---|
| A-s101 | 58 (51 resolved) | 40 (13) | 1 (0) | 1 (0) | 64 |
| A-s202 | 47 (40) | 50 (19) | 2 (0) | 1 (0) | 59 |
| C-s101 | 80 (62) | 12 (5) | 7 (4) | 1 (0) | 71 |
| C-s202 | 76 (60) | 13 (7) | 11 (1) | 0 | 68 |

- **What changed.** Guard stops fell from 40–50 to 12–13 per seed. Most
  freed runs ended with the model stopping on its own.
- **A new failure mode.** 7 and 11 runs now reach the 40-step cap, and
  only 5 of those 18 resolved.

### Cost

| Arm | Input | Cached | Uncached | Output | LLM calls | Input per resolved |
|---|---|---|---|---|---|---|
| A-s101 | 7.26M | 92.2% | 0.56M | 251k | 1,299 | 113k |
| A-s202 | 7.49M | 92.6% | 0.55M | 264k | 1,258 | 127k |
| C-s101 | 9.82M | 93.4% | 0.64M | 269k | 1,473 | 138k |
| C-s202 | 12.20M | 93.8% | 0.75M | 298k | 1,555 | 179k |
| R | 14.65M | 92.9% | 1.04M | 366k | 1,658 | 209k |

**Against the baseline.** Over both seeds, C uses:

- input ×1.49 (uncached ×1.25);
- output ×1.10;
- calls ×1.18;
- input per resolved instance ×1.32.

**Against R.** C averages 11.0M input to R's 14.65M, and 1,514 calls to
R's 1,658.

## Operational findings

**The GLM Coding Plan quota appears to cap calls, not tokens.** This is
inferred from two runs that started with a fresh window:

| Run | Instances before the quota ran out | LLM calls by then | Tokens per run |
|---|---|---|---|
| R | 84 | ~1,392 | higher |
| C-s202 | 89 | ~1,383 | lower |

The two runs stopped at almost the same number of calls despite their
different token totals. C-s101's first window, which was not fresh, ended
after 54 instances.

**Changes to the tooling.**

- **Attempt cap.** The runner's `--max-attempts` (default 2, so the formal
  runs are unchanged) lets a later invocation retry instances whose
  attempts the quota used up.
- **Regrading.** The dev script regrades each seed in full, because the
  harness keeps any report it already has for an instance under the run
  ID.
- **Summary.** The dev script's summary had dropped seeds run by an
  earlier invocation. It now includes every graded seed.

## DeepSeek V4.1 Flash

Does the gain carry over to another model? Arms DA (cumulative) and DC
(consecutive) repeat A and C with profile `deepseek_flash_nothink`
(`deepseek-flash`, thinking off) and 4 workers. Everything else is the
same: sample, processing order, seeds, preset, step and wall caps. The
script is `scripts/wp1_dev_deepseek.sh`, at agent `ac9d266`.

**Evidence.** `artifacts/wp1-dev/deepseek-outcomes.json` and
`deepseek-pooled.json`, transcribed from the host as for the C arms. The
per-instance outcomes reproduce all six pair tables the host printed, and
the pooled comparison recomputed from them matches the host's.

**A bug found first.** The first DeepSeek run (seed 101) ended 4 baseline
and 9 candidate runs with HTTP 400: "An assistant message with
'tool_calls' must be followed by tool messages".

- **Cause.** When the same failing call repeated, the doom-loop warning
  was added to the history before the tool results.
- **Evidence.** A read-only check on the host found that all 13 failures
  came right after a warning, at the run's last tool step.
- **Fix.** `36d6300` adds the warning after the results, with a
  regression test.
- **Scope.** That run is discarded. GLM accepted the malformed history,
  so the GLM arms above ran with the warning misplaced, equally in both
  arms.

### Results

| Seed | DA | DC | Only DA | Only DC | Exact McNemar p |
|---|---|---|---|---|---|
| 101 | 52 | 56 | 6 | 10 | 0.45 |
| 202 | 57 | 59 | 6 | 8 | 0.79 |

| Pooled per instance | Value |
|---|---|
| Resolved runs, DA / DC | 109 / 115 of 200 |
| Difference | +3.0 pp (95% bootstrap −2 to +8) |
| Exact sign-flip p (two-sided) | 0.35 |
| Score difference DC − DA, by instances | +2: 3, +1: 8, 0: 81, −1: 8 |
| Resolved in both seeds, DA / DC | 46 / 48 |

No run ended in a loop exception or a provider failure.

### Where the freed runs went

Termination over both seeds. In parentheses: how many resolved.

| | Baseline GLM | Candidate GLM | Baseline DeepSeek | Candidate DeepSeek |
|---|---|---|---|---|
| model_stop | 105 (91) | 156 (122) | 96 (86) | 113 (98) |
| retry_exhausted | 90 (32) | 25 (12) | 63 (19) | 17 (6) |
| max_steps | 3 (0) | 18 (5) | 41 (4) | 70 (11) |
| loop_exception | 2 (0) | 1 (0) | 0 | 0 |

- **GLM.** The guard stopped 65 fewer runs. About four in five of them
  ended with the model stopping on its own.
- **DeepSeek.** The guard stopped 46 fewer runs. Only 17 of them ended
  with the model stopping; 29 reached the 40-step cap, most of those with
  an empty or failing patch.
- **The binding limit differs.** On DeepSeek it is the step budget, not
  the guard: even its baseline reaches the cap in about one run in five,
  against one in seventy on GLM.

### Against mini-SWE-agent

Arm DR is arm R on DeepSeek: mini-SWE-agent 2.4.6 with the same settings,
one run (`PROFILE=deepseek_flash_nothink scripts/wp1_reference_arm.sh`, at
`07eae78`). It resolved 64 of 100.

| Model | mini-SWE-agent | Baseline, mean of 2 seeds | Candidate, mean of 2 seeds |
|---|---|---|---|
| glm-5.1 | 70 | 61.5 | 69.5 |
| DeepSeek V4.1 Flash | 64 | 54.5 | 57.5 |

| Pair | Both | Only first | Only DR | Exact McNemar p |
|---|---|---|---|---|
| DA-s101 vs DR | 47 | 5 | 17 | 0.017 |
| DA-s202 vs DR | 51 | 6 | 13 | 0.17 |
| DC-s101 vs DR | 49 | 7 | 15 | 0.13 |
| DC-s202 vs DR | 50 | 9 | 14 | 0.40 |

- **The step cap binds there too.** mini-SWE-agent also runs out of steps
  more often on DeepSeek: 23 of its runs hit the 40-call limit, against 8
  on glm-5.1.
- **A different consequence.** A run that hits the limit submits nothing,
  so all 23 have empty patches. Our Agent keeps the working-tree diff at
  the cap, and 11 of the candidate's 70 capped runs resolved.
- **Data quality.** The harness reported one ambiguous failure, and one
  instance has partial token usage.

### Cost

| | DA | DC | Ratio |
|---|---|---|---|
| Input | 32.75M | 44.88M | ×1.37 |
| Cached | 96.0% | 96.5% | |
| Uncached input | 1.32M | 1.58M | ×1.19 |
| Output | 576k | 645k | ×1.12 |
| LLM calls | 3,661 | 4,364 | ×1.19 |
| Input per resolved run | 300k | 390k | ×1.30 |

At DeepSeek's peak list prices (CNY per million tokens: cache hit 0.04,
miss 2, output 8), the 400 runs cost about ¥18.6, and arm DR's 100 about
¥6.3. Off-peak prices are half.

| Per run | DA | DC | DR |
|---|---|---|---|
| Input | 164k | 224k | 227k |
| LLM calls | 18.3 | 21.8 | 20.2 |
| Output | 2.9k | 3.2k | 4.1k |
| Input per resolved run | 300k | 390k | 355k |

## Limitations

- **Selection.** The policy was designed from this sample's failures, so
  the +8 pp estimate is optimistic.
- **Model dependence.** The +8 pp is glm-5.1's, with thinking off,
  through the GLM Coding Plan endpoint. On DeepSeek V4.1 Flash the effect
  was +3 pp and not significant.
- **A bug in the GLM arms.** The GLM arms ran with the doom-loop warning
  misplaced (fixed in `36d6300`). It affected both arms, but the GLM
  numbers are not from the current code.
- **Transcription.** The C arms' outcomes were transcribed from the host,
  not copied with their hashes.
- **Timing.** The instances rerun after a quota reset ran hours after the
  rest of their seed, so any provider-side change in between is not
  controlled.

## Next: held-out confirmation

A pre-registered run would compare A and C on Verified instances outside
the pilot and formal samples (390 remain).

**Power.** Computed from these data by normal approximation, using the
per-instance standard deviation of 0.34 with two seeds.

| Design | Runs | If the effect is +8 pp | If it is +6 pp | Instances left unused |
|---|---|---|---|---|
| 150 instances, 2 seeds per arm | 600 | 82% | 58% | 240 |
| 200 instances, 2 seeds per arm | 800 | 92% | 71% | 190 |
| All 390 instances, 1 seed per arm | 780 | 95% | 77% | 0 |

**Quota.** The arms average about 12.8 (A) and 15.1 (C) calls per run.
The 200 × 2 design therefore needs about 11,200 calls, around eight GLM
Coding Plan windows.

**Model.** The effect to confirm is glm-5.1's, so a held-out run should
use glm-5.1, at the current code, in both arms. DeepSeek's binding limit
is the step budget, which calls for a different change.

**Open decisions.**

- the sample size;
- whether to keep the Coding Plan quota or move to a pay-as-you-go
  endpoint.
