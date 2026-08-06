# Improvement Report: v0.7.4-knowledge

## What Changed

Added an opt-in Agent Knowledge integration backed by the retrieval API in
`rag-benchmark-system`.

### Files Added

- `coder_agent/tools/knowledge_retrieval.py` — non-blocking HTTP retrieval tool with source/page formatting, bounded `top_k`, timeout handling, and defensive response validation.
- `tests/test_knowledge_retrieval.py` — success, validation, HTTP failure, malformed response, empty-result, and registry opt-in coverage.

### Files Modified

- `coder_agent/core/tool_registry.py`
  - Registers `KnowledgeRetrievalTool` only when `RAG_API_URL` is non-empty.
  - Leaves the default and accepted benchmark tool sets byte-for-byte equivalent when the integration is disabled.

## Intended Effect on Agent Behavior

When `RAG_API_URL` is configured, the model receives a
`knowledge_retrieval` tool and can search pre-indexed documents before editing
or answering. Results include the source filename and page range supplied by
the RAG service.

When `RAG_API_URL` is absent, the tool is not registered. This avoids offering
an unusable tool and preserves the existing default/C3/C4/C6 execution path.

## Rebaseline Required?

- **Accepted C3/C4/C6 lanes with `RAG_API_URL` unset:** no new baseline is
  claimed or required for this patch because their registered tool set and
  runtime path are unchanged. The registry test enforces this invariant.
- **Any lane or preset that enables `RAG_API_URL`:** rebaseline is required
  before it can be promoted or compared against accepted C3/C4/C6 numbers,
  because the available tool set and model decision surface change.

No benchmark uplift is claimed by this report. The local regression gate is
`275 passed`; the cross-project closure test is tracked separately by the
[portfolio closure plan](https://github.com/MichLitt/agent-systems-portfolio/blob/main/docs/plans/three-project-closure-plan.md).

## How to Enable

Start `rag-benchmark-system`, register or ingest an index, then export:

```bash
export RAG_API_URL=http://localhost:8080
```

`build_tools()` will then include `knowledge_retrieval`. Unset the variable to
return to the accepted default tool set.
