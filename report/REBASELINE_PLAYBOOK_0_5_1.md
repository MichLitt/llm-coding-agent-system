# Rebaseline Playbook 0.5.1

> Date: 2026-04-05
> Scope: active rebaseline playbook for the current `0.5.1` branch state

## Goal

`0.5.1` is the current rebaseline cycle for the 40-task Custom suite. This cycle does **not** accept the older `v050/v051/v052` exploratory runs as final public metrics. The goal is to produce a fresh, auditable final artifact set on the current codebase after the eval auditability fixes landed.

## Preset Policy

- Active day-to-day presets: `default`, `C3`, `C4`, `C6`
- Promoted benchmark candidates for the `0.5.1` cycle: `C4`, `C6`
- Supporting compare lane: `C3`
- Experimental/non-promoted preset: `C5`

Interpretation:

- `C4` is the memory-enabled lane and now includes default approach-memory support
- `C6` remains the verification-gated lane for stop-discipline-sensitive runs
- `C3` remains the clean supporting compare lane

## Runtime Contract

The supported runtime path remains an OpenAI-compatible backend:

- required: `LLM_API_KEY`
- optional: `LLM_BASE_URL`
- optional: `CODER_MODEL`

Run manifests must capture:

- preset `agent_config`
- runtime `experiment_config` overrides
- separate hashes for each snapshot
- the combined config snapshot hash used for audit and resume

## Local Gate

```bash
uv run pytest
uv run python -m coder_agent --help
uv run python -m coder_agent eval --help
```

## Required Final Artifact Set

These are the required final Custom artifacts for the `0.5.1` cycle.

### Memory lane

```bash
uv run python -m coder_agent eval --benchmark custom --preset C4 --config-label c4_m1_final
uv run python -m coder_agent eval --benchmark custom --preset C4 --config-label c4_m3_final --experiment-config '{"memory_lookup_mode":"similarity"}'
```

Interpretation:

- `c4_m1_final` is the promoted C4 default lane
- `c4_m3_final` is a supporting optional similarity-retrieval lane

### C6 context confirmation matrix

```bash
uv run python -m coder_agent eval --benchmark custom --preset C6 --config-label c6_baseline_final
uv run python -m coder_agent eval --benchmark custom --preset C6 --config-label c6_ctx1_final --experiment-config '{"doom_loop_threshold":2}'
uv run python -m coder_agent eval --benchmark custom --preset C6 --config-label c6_ctx2_final --experiment-config '{"observation_compression_mode":"smart"}'
uv run python -m coder_agent eval --benchmark custom --preset C6 --config-label c6_ctx3_final --experiment-config '{"history_compaction_mode":"semantic"}'
uv run python -m coder_agent eval --benchmark custom --preset C6 --config-label c6_ctx_all_final --experiment-config '{"doom_loop_threshold":2,"observation_compression_mode":"smart","history_compaction_mode":"semantic"}'
```

## Artifact Rules

- Keep `results/*.json` as the metric source of truth.
- Keep matching `*_run_manifest.json` files for auditability and reproducibility.
- Keep matching `trajectories/*.jsonl` files for failure analysis.
- Cite only exact final artifact names in README and public reports.
- Treat `v050/v051/v052` artifacts as historical exploratory data, not accepted final metrics.

## Acceptance Rules

`0.5.1` is accepted only when all of the following are true:

1. The local gate passes.
2. All required final artifacts listed above exist.
3. Each required artifact has a matching manifest and trajectory file.
4. Each manifest records preset config, runtime config, and separate snapshot hashes.
5. `report/BASELINE_0_5_1.md` is updated from pending status to accepted status with exact artifact references.
6. README cites the final accepted `0.5.1` artifact names instead of only the archived `0.4.0` set.
