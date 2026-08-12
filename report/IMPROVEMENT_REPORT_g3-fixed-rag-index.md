# Improvement Report — G3 fixed RAG index

Date: 2026-08-12
Owner: Agent runtime
Delivery-gate impact: G3 preparation only; no quality gate is advanced.

## Change

`knowledge_retrieval` now supports a run-scoped fixed index policy. When
`rag_index_id` is set, the tool schema advertises that index and every request
uses it even if the model submits a different `index_id`.

## Intended effect

The G3 candidate condition is bound to the exact corpus named by its frozen
manifest. It cannot silently retrieve from a default, stale, or model-selected
index. The baseline continues to remove the tool entirely.

## Compatibility and rollback

Without `rag_index_id`, retrieval keeps its original caller-selected/default
index behavior. Removing the optional factory and tool arguments restores that
behavior without a data migration or public API change.

## Verification

- Targeted tests cover index overriding, schema/argument validation, registry
  forwarding, and the existing retrieval/eval CLI paths.
- Full Agent suite is required before merge.

## Rebaseline requirement

Required for the G3 candidate condition because its tool policy changes. Prior
benchmark artifacts remain historical and cannot substitute for G3 evidence.
