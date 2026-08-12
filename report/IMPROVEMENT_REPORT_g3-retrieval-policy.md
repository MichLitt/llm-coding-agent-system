# Improvement Report — G3 run-scoped retrieval policy

Date: 2026-08-12
Owner: Agent runtime
Delivery-gate impact: G3 preparation only; no quality gate is advanced.

## Change

The Agent factory now consumes `knowledge_retrieval` from the run-scoped
experiment configuration, with a preset value only as a fallback. The value is
strictly boolean when supplied. This makes the documented `coder_agent eval
--experiment-config` command control actual tool registration.

## Intended effect

The G3 baseline can disable retrieval while the RAG service remains configured;
the candidate can enable it under the same environment, model, task set, and
budget. This prevents an environment-only setting from invalidating the
comparison.

## Compatibility and rollback

Calls with no run-scoped policy preserve the prior behavior. Removing the
resolver and its factory use reverts the change without data migration or API
contract changes.

## Verification

- Targeted configuration and retrieval tests cover explicit false, explicit
  true, preset fallback, and invalid non-boolean values.
- Full Agent suite is required before merge.

## Rebaseline requirement

Required. This is a tool-policy behavior change. Existing benchmark artifacts
remain historical only and cannot be substituted for the planned G3 evidence.
