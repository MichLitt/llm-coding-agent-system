# Improvement Report — G3 retrieval toggle

Date: 2026-08-12
Owner: Agent runtime
Delivery-gate impact: G3 preparation only; no quality gate is advanced.

## Change

`build_tools()` now accepts an explicit `enable_knowledge_retrieval` policy.
When RAG is configured, callers can force the retrieval tool off for a
baseline run or on for the candidate run. With no explicit policy, the prior
environment-driven behavior remains unchanged.

## Intended effect

This makes the future Agent-versus-RAG ablation controlled: both conditions can
run against the same service environment, while differing only in whether the
Agent is allowed to register `knowledge_retrieval`.

## Compatibility and rollback

Existing callers omit the new keyword-only argument and retain the former
behavior. Removing the optional argument and factory forwarding restores the
previous implementation without data migration or external contract change.

## Verification

- Targeted retrieval and evaluation-CLI tests: 22 passed.
- Root protocol validation confirms a 20-task manifest, three distinct seeds,
  matching corpus/index identity, and a pinned manifest digest.

## Rebaseline requirement

Required. Tool-set behavior is part of the Agent baseline integrity rules.
The accepted 0.7.1 artifacts remain historical evidence only; they must not be
used as the G3 baseline or candidate result. The G3 evidence run is pending
authorized model credentials and compute budget.
