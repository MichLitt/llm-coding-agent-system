# Rebaseline Playbook 0.4.0

> Date: 2026-03-11
> Scope: final accepted same-code baseline for `0.4.0`

## Goal

`0.4.0` now uses a final accepted baseline generated from the current codebase on March 11, 2026.

Historical `v8`/`v9` benchmark tables and the first `0.4.0` RC rerun remain useful archive material, but they are no longer the public source of truth.

## Preset Policy

- Promoted benchmark candidates: `C3`, `C4`, `C6`
- Active day-to-day presets: `default`, `C3`, `C4`, `C6`
- Experimental/non-promoted preset: `C5`
- Compatibility-only presets: `C1`, `C2`

Interpretation:

- `C3` remains the clean ReAct + correction path
- `C4` remains the memory-enabled path for multi-step Custom tasks
- `C6` remains the verification-gated path for HumanEval and stop-discipline-sensitive runs

## Runtime Contract

The supported runtime path for `0.4.0` is an OpenAI-compatible backend:

- required: `LLM_API_KEY`
- optional: `LLM_BASE_URL`
- optional: `CODER_MODEL`

`model.provider` remains compatibility-only metadata in `config.yaml`.

## Accepted Final Command Set

Local gate:

```powershell
uv run pytest
uv run python -m coder_agent --help
Write-Output '/exit' | uv run python -m coder_agent
```

Final accepted benchmark commands:

```powershell
uv run python -m coder_agent eval --benchmark humaneval --preset C3 --resume --config-label humaneval_040_final_c3
uv run python -m coder_agent eval --benchmark humaneval --preset C6 --resume --config-label humaneval_040_final_c6
uv run python -m coder_agent eval --benchmark custom --preset C4 --resume --config-label custom_040_final_c4
uv run python -m coder_agent eval --benchmark custom --compare C3,C4,C6 --resume --config-label custom_040_final_cmp
uv run python -m coder_agent eval --benchmark custom --compare C4,C6 --resume --config-label custom_040_final_cmp_retry
```

Accepted interpretation of the Custom compare commands:

- `custom_040_final_cmp_C3` is the retained clean `C3` lane from the first compare run
- `custom_040_final_cmp_retry_C4` and `custom_040_final_cmp_retry_C6` are the accepted retry-recovered compare artifacts for `C4` and `C6`
- the original `custom_040_final_cmp_C4` and `custom_040_final_cmp_C6` are superseded polluted artifacts, retained only for audit/history

## Acceptance Result

Local gate:

- `uv run pytest` passed with `60/60`
- `uv run python -m coder_agent --help` passed
- default REPL startup and `/exit` passed

Final accepted metrics:

| Artifact | Preset | Result | Notes |
|----------|--------|--------|-------|
| `humaneval_040_final_c3` | `C3` | `157/164 = 95.7%` | supporting HumanEval reference |
| `humaneval_040_final_c6` | `C6` | `161/164 = 98.2%` | promoted HumanEval baseline |
| `custom_040_final_c4` | `C4` | `19/21 = 90.5%` | standalone memory-enabled Custom run |
| `custom_040_final_cmp_C3` | `C3` | `20/21 = 95.2%` | supporting clean compare artifact retained from the first compare run |
| `custom_040_final_cmp_retry_C4` | `C4` | `20/21 = 95.2%` | retry-recovered memory-enabled compare artifact |
| `custom_040_final_cmp_retry_C6` | `C6` | `21/21 = 100.0%` | promoted final Custom baseline |

Accepted interpretation:

- promote `humaneval_040_final_c6` as the primary HumanEval result
- promote `custom_040_final_cmp_retry_C6` as the primary Custom result for the final cycle
- keep `custom_040_final_cmp_C3` as the clean supporting `C3` compare artifact
- keep `custom_040_final_cmp_retry_C4` as the supporting memory-enabled compare artifact
- keep `custom_040_final_c4` as the standalone memory-enabled Custom reference
- retain `custom_040_final_cmp_C4` and `custom_040_final_cmp_C6` as superseded polluted audit artifacts only

## Artifact Rules

- Keep `results/*.json` as the metric source of truth.
- Keep `*_run_manifest.json` files for resume and auditability.
- Keep matching `trajectories/*.jsonl` files for analysis and failure taxonomy.
- Cite final artifacts by exact artifact name in README and public reports.
- Keep `BASELINE_0_4_0_RC.md` as archive/reference only.

## Release Acceptance

`0.4.0` is accepted when all of the following are true:

1. `uv run pytest` passes.
2. `uv run python -m coder_agent --help` passes.
3. default REPL startup exits cleanly.
4. final accepted benchmark artifacts exist for the required runs.
5. README and public reports cite final accepted artifacts from the current codebase.
6. the branch is no longer described as a release candidate in the primary docs.
