# Improvement Report — G3 model seed propagation

Date: 2026-08-12
Owner: Agent runtime
Delivery-gate impact: G3 preparation only; no quality gate is advanced.

## Change

The Agent model configuration now carries a seed. A run-scoped `model_seed`
override is type-checked, recorded in the existing run manifest's runtime
configuration snapshot, and forwarded to OpenAI-compatible completion requests.
Anthropic-compatible transports keep the seed as experiment metadata because
their portable request contract has no seed parameter.

## Intended effect

The G3 `glm_5` profile uses OpenAI-compatible transport, so every baseline and
candidate invocation for a given seed receives the same provider seed. This
makes the three-seed protocol executable rather than merely descriptive.

## Compatibility and rollback

Runs without `model_seed` retain the configured default. Removing the model
field and request forwarding returns to the previous provider calls; no stored
data or public HTTP contract changes.

## Verification

- Targeted tests cover run-scoped seed selection, invalid values, OpenAI request
  forwarding, Agent loop calls, and context compaction calls.
- Full Agent suite is required before merge.

## Rebaseline requirement

Required for any benchmark that changes the seed from the accepted run
configuration. Existing artifacts remain historical and cannot substitute for
the planned G3 evidence.
