# SWE-bench summary (100 instances)

Instances file sha256: `3e045070300bd3147cae5e9977cb830722cee50962f2a57e7ba952c5ae6d1936`. Recompute with `python -m coder_agent.eval.swebench_summary`.

| Arm | Resolved | Rate (95% Wilson) | Infra | Provider | Harness | Wall cap | Output tokens |
|---|---|---|---|---|---|---|---|
| A-s101 | 64/100 | 64.0% (54.2%–72.7%) | 0 | 0 | 0 | 0 | 251384 |
| A-s202 | 59/100 | 59.0% (49.2%–68.1%) | 0 | 0 | 0 | 0 | 264383 |
| R | 70/100 | 70.0% (60.4%–78.1%) | 0 | 0 | 0 | 0 | 366347 |

| Pair | Both | Only first | Only second | Neither | Exact McNemar p | Rate diff (95% bootstrap) |
|---|---|---|---|---|---|---|
| A-s101 vs A-s202 | 52 | 12 | 7 | 29 | 0.3593 | +5.0% (-3.0% to +14.0%) |
| A-s101 vs R | 58 | 6 | 12 | 24 | 0.2379 | -6.0% (-14.0% to +2.0%) |
| A-s202 vs R | 51 | 8 | 19 | 22 | 0.0522 | -11.0% (-21.0% to -1.0%) |

A-s101 termination by outcome: loop_exception: empty_patch 1; max_steps: unresolved 1; model_stop: empty_patch 1, resolved 51, unresolved 6; retry_exhausted: empty_patch 3, resolved 13, unresolved 24

A-s202 termination by outcome: loop_exception: empty_patch 1; max_steps: empty_patch 1, unresolved 1; model_stop: empty_patch 1, resolved 40, unresolved 6; retry_exhausted: empty_patch 4, resolved 19, unresolved 27

R termination by outcome: LimitsExceeded: empty_patch 8; RepeatedFormatError: empty_patch 1; Submitted: resolved 70, unresolved 21
