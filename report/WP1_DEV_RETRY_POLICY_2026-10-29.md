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

## Limitations

- **Selection.** The policy was designed from this sample's failures, so
  the +8 pp estimate is optimistic.
- **One model and one endpoint.** Everything ran on glm-5.1 with thinking
  off, through the GLM Coding Plan endpoint.
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

**Open decisions.**

- the sample size;
- whether to keep the Coding Plan quota or move to a pay-as-you-go
  endpoint;
- whether to stay on glm-5.1, since a different model would test a
  different question.
