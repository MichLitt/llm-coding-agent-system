# Improvement Report: v0.5.2 — Multi-API / Multi-Model Profile System

## Summary

v0.5.2 introduces a named-profile LLM configuration system. The previous single-backend model (one global `ModelConfig`, global `cfg.model.*` mutation at runtime) is replaced by a profile-first architecture that supports multiple provider endpoints simultaneously, is safe for concurrent use, and produces auditable eval manifests.

**Default shipped baseline is unchanged.** The accepted 0.5.1 benchmark results remain the active source of truth. No rebaseline is required.

---

## Motivation

The previous implementation had three structural limitations:

1. **Single global backend** — `ModelConfig` could only represent one provider at a time. Switching required mutating `cfg.model.name` or `cfg.model.api_format` globally.
2. **Global state mutation** — `make_agent(model=...)` wrote to `cfg.model.name`, making it unsafe for concurrent runs or future API service layers.
3. **No LLM audit trail in eval manifests** — there was no record of which model or provider was used in a given eval run.

---

## Changes

### Config layer (`config.yaml`, `coder_agent/config.py`)

- New `llm.profiles` block in `config.yaml` with three built-in profiles: `minimax_m27`, `minimax_m25`, `glm_5`.
- New `LLMProfile` dataclass: immutable, carries `name`, `transport`, `model`, `api_key`, `base_url`.
- New `resolve_llm_profile(name: str | None) -> LLMProfile` function — reads from `llm.profiles`, falls back to legacy `model:` block if not present.
- Legacy `model:` block retained for backward compatibility.

### Client layer (`coder_agent/core/llm_client.py`)

- `LLMClient(profile: LLMProfile | None)` — explicit profile binding instead of reading global `cfg.model.*`.
- `client.profile` attribute available for downstream code (e.g. `decomposer.py`) to read the resolved model name.

### Factory layer (`coder_agent/cli/factory.py`)

- `make_agent(llm_profile: str | None = None, ...)` — new parameter.
- Removed `cfg.model.name = model` global mutation. The `--model` override now only mutates the local `LLMProfile` copy for that agent.
- `make_session()` similarly updated.

### CLI (`coder_agent/cli/main.py`, `chat.py`, `eval.py`)

- All three commands accept `--llm-profile <profile_name>`.
- Priority: `--llm-profile` → `llm.default_profile` in config.yaml → legacy `model:` block.

### Eval manifest (`coder_agent/eval/eval_checkpoint.py`, `runner.py`)

- `write_run_manifest()` now records `llm_profile`, `llm_model`, `llm_transport`.
- `EvalRunner.__init__(llm_profile_name=...)` resolves and stores the profile at construction time.

### Environment variables

All profile env vars follow the unified `LLM_{PROFILE_UPPER}_API_KEY` / `LLM_{PROFILE_UPPER}_BASE_URL` pattern:

| Profile | API Key | Base URL |
|---|---|---|
| `minimax_m27` | `LLM_MINIMAX_M27_API_KEY` | `LLM_MINIMAX_M27_BASE_URL` |
| `minimax_m25` | `LLM_MINIMAX_M25_API_KEY` | `LLM_MINIMAX_M25_BASE_URL` |
| `glm_5` | `LLM_GLM_5_API_KEY` | `LLM_GLM_5_BASE_URL` |

Old vars (`ANTHROPIC_API_KEY`, `LLM_API_KEY`, etc.) remain active in the legacy fallback path.

---

## Smoke Test Results

| Profile | Transport | Outcome |
|---|---|---|
| `minimax_m27` | anthropic | **Pass** — agent completed 3-step task, exit OK |
| `glm_5` | openai | Profile resolved correctly; API returned 429 (insufficient balance on test account) — **not a code issue** |

GLM-5 profile resolution, env var parsing, and `_OpenAIBackend` selection all work correctly. The 429 error is a billing issue with the specific test API key.

---

## Backward Compatibility

- Projects with no `llm:` block in `config.yaml` automatically fall back to the `model:` block — no config change required.
- All existing CLI flags (`--model`, `--no-memory`, etc.) continue to work.
- Accepted 0.5.1 baseline artifact names and metrics are unchanged.

---

## Rebaseline Decision

**Not required.** The default profile (`minimax_m27`) maps to the same model and API endpoint as the 0.5.1 shipped configuration. No benchmark results are affected.

If a future run with `--llm-profile glm_5` produces results worth promoting, those should be recorded as a new experiment artifact, not written into the accepted 0.5.1 baseline.

---

## Next Steps (deferred)

- **v0.5.3**: Profile-aware eval comparison — systematic comparison of `glm_5` vs `minimax_m27` on the Custom suite.
- **v0.6.0**: Persistent run state, resume/retry, and runtime API service layer.
