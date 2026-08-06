# AGENTS.md

When this repository is checked out inside `agent-systems-portfolio`, also read
`../AGENTS.md` and `../docs/engineering/ENGINEERING_GUIDE.md` for shared
cross-project contracts. This file remains the complete local entry point for
a standalone clone and adds the stricter Agent Runtime rules.

## Commands

- Install: `uv sync && cp .env.example .env`
- Run agent (interactive): `uv run python -m coder_agent`
- Run tests: `uv run pytest`
- Run single task: `uv run python -m coder_agent run "<task>"`
- Analyze results: `uv run python -m coder_agent analyze <config-label>`
- Run benchmark: see current REBASELINE_PLAYBOOK for accepted commands

## Environment

- `LLM_<PROFILE>_API_KEY` — credential for the selected named profile
- `LLM_<PROFILE>_BASE_URL` — optional provider endpoint override
- `RAG_API_URL` — optional; enables `knowledge_retrieval` when non-empty
- `EVALOPS_ENDPOINT` — optional Agent endpoint `/v1/ingest/agent/v1`
- `EVALOPS_API_KEY` — optional bearer token
- `model.provider` in `config.yaml` is informational only; runtime ignores it

Do not expose `knowledge_retrieval` when `RAG_API_URL` is unset. Changes to the
RAG request/response contract require Agent + RAG tests and the root closure
script.

## Current Baseline

Before changing agent behavior or citing benchmark numbers, read:
- `report/REBASELINE_PLAYBOOK_<latest version>.md` — accepted preset policy, benchmark artifacts, and rebaseline trigger conditions
- `report/BASELINE_<latest version>.md` — accepted metric source of truth

## Baseline Integrity Rules

A rebaseline is required if you change any of the following for C3, C4, or C6:
- Agent loop logic (`core/agent_loop.py`, `core/agent.py`)
- System prompt (`core/agent_prompt.py`)
- Tool behavior (`tools/`)
- Context compression strategy (`core/context.py`)
- Verification gate behavior

A rebaseline is NOT required for:
- Eval/analysis tooling that doesn't touch agent behavior
- CLI/UX changes
- Test additions
- Documentation

Never cite benchmark numbers without artifact backing.
Artifact source of truth: `results/*.json` + matching `trajectories/*.jsonl`.
Do NOT overwrite existing baseline files — create a new versioned file instead.

## Automated Report Writing

After completing any of the following, write a report in `report/` before closing the task:

**Feature / behavior change → `report/IMPROVEMENT_REPORT_v<tag>.md`**
Include:
- What changed and which files were modified
- Intended effect on agent behavior
- Whether a rebaseline is required (per rules above)
- Tag: next patch version or short descriptor (e.g. `v0.4.5`, `v0.4.5-memory-fix`)

**Refactor / architecture change → `report/REFACTOR_REPORT_v<tag>.md`**
Include:
- What was restructured and why
- Which interfaces changed
- Backward compatibility notes

Do not write a report for:
- Test-only changes
- Doc fixes
- Config tweaks that don't affect agent behavior

## Auto-Maintained Docs

After any structural change, check and update these if stale:
- `README.md` — preset table, quick start commands, current version
- The active `report/REBASELINE_PLAYBOOK_<version>.md` — preset policy, trigger conditions

Do not create new docs unless asked. Update in place.
