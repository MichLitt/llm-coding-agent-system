# v0.8.0 Frozen Baseline Playbook

Status: preregistered commands; no accepted quality result yet.

## Frozen inputs

- Runtime: the committed v0.8.0 implementation snapshot once code review is
  complete; no Agent-loop change is allowed between runs.
- Profile: `glm_5`; preset: `C3`; seeds: `101`, `202`, `303`.
- Conversation: hash-frozen test split at
  `coder_agent/eval/benchmarks/conversation/test`.
- Complex: six local tasks at `coder_agent/eval/benchmarks/complex_code`.
- SWE: the promoted 12-task manifest generated from the pinned Lite revision
  `6ec7bb89b9342f664a54a6e0a6ea6501d3437cc2`.

## Commands

Run ConversationBench and ComplexCodeBench for each seed, changing only the
label and `model_seed`:

```bash
uv run python -m coder_agent eval --benchmark conversation \
  --task-dir coder_agent/eval/benchmarks/conversation/test \
  --preset C3 --llm-profile glm_5 \
  --config-label v080_conversation_c3_seed101 \
  --output artifacts/v080-baseline \
  --experiment-config '{"model_seed":101}'

uv run python -m coder_agent eval --benchmark complex \
  --preset C3 --llm-profile glm_5 \
  --config-label v080_complex_c3_seed101 \
  --output artifacts/v080-baseline \
  --experiment-config '{"model_seed":101}'
```

Run the SWE-12 operational baseline once before deciding whether its cost and
environment reliability justify three seeds:

```bash
uv run python -m coder_agent eval --benchmark swebench --swebench-subset promoted \
  --preset C3 --llm-profile glm_5 \
  --config-label v080_swe12_c3_seed101 \
  --output artifacts/v080-baseline \
  --experiment-config '{"model_seed":101}'
```

## Reporting boundary

Retain every raw manifest, JSONL/result file, failed run, and timeout. The
baseline report must state numerator/denominator, task IDs, seeds, model
profile, environment failures, token/latency summaries, and failure taxonomy.
No candidate runtime change may be implemented until these artifacts are
summarized in `BASELINE_0_8_0.md`.

