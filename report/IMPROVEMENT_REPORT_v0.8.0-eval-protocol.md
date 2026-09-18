# v0.8.0 Evaluation Protocol Implementation Report

Status: local protocol implementation in progress; no accepted quality baseline.

## Implemented

- ConversationBench v1 has versioned task/turn schemas, a same-session runner,
  per-turn phase and retained-constraint checks, task-boundary-only resume, raw
  JSONL turn artifacts, summaries, and provenance manifest fields.
- Six development conversations and twelve hash-frozen test conversations cover
  clarification, incremental requirements, correction, regression preservation,
  contextual references, and scope control.
- ComplexCodeBench v1 has an independent task/complexity schema and six local,
  deterministic fixtures covering migration/idempotency, compatibility parsing,
  recovery/checkpointing, concurrent deduplication, validation boundaries, and
  module contracts. Its clean buggy-fail/gold-pass replay audit runs each task
  three times.
- The MCP adapter is default-off and stdio-first. It discovers an explicit
  allowlisted server configuration at run start, records discovery/call audit
  metadata, and tears down the process at session end. Remote transports,
  OAuth, dynamic credentials, and automatic server discovery are excluded.
- A model-blind, pinned-revision SWE expansion is now promoted: four additional
  tasks extend the accepted lane to 12 tasks across 8 upstream repositories.
  Every new task has an explicit local environment override and completed three
  clean buggy-fail/gold-pass/PASS_TO_PASS replay audits.

## Verification

- Conversation fixture/schema/freeze tests exercise deterministic runner,
  constraint failures, clarification-before-action, task-boundary resume, and
  fixture freeze mismatches.
- Complex replay tests run all six fixtures three times from clean workspaces.
- SWE candidate import is pinned to `princeton-nlp/SWE-bench_Lite` revision
  `6ec7bb89b9342f664a54a6e0a6ea6501d3437cc2`; promotion is covered by the
  checked-in source, generated manifest, overrides, and loader alignment test.
- Latest full owner suite after SWE-12 promotion: `310 passed`.

## Baseline boundary

No paid-model or accepted benchmark baseline was run. The current v0.7.x
quality figures must not be compared with ConversationBench or ComplexCodeBench
until the frozen baseline
commands/artifacts are complete. This report makes no quality-improvement
claim.

## Remaining v0.8.0 work

- Run frozen Conversation/Complex/SWE baselines with the plan's seed policy;
  preserve raw artifacts and write `BASELINE_0_8_0.md`.
