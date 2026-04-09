# REBASELINE_PLAYBOOK_0_7_1

Status: accepted closure playbook for the `0.7.1` cycle.

## Scope

- `run_command` pipefail semantics
- SWE verification overlay conflict handling
- `sphinx-doc__sphinx-8273` task-local setup tightening
- layered taxonomy tightening for `shell_exit_masking`

## Required Local Gates

```bash
uv run pytest tests/test_analysis.py
uv run pytest
uv run python -m coder_agent analyze swe_promoted_cmp_v071r1_C3
uv run python -m coder_agent analyze swe_promoted_cmp_v071r1_C6
uv run python -m coder_agent analyze swe_promoted_support_v071r1_C4
uv run python -m coder_agent analyze swe_probe_pytest7373_v071c
uv run python -m coder_agent analyze swe_probe_flask4992_v071
uv run python -m coder_agent analyze swe_probe_sphinx8273_v071
```

## Accepted Artifact Families

Formal promoted compare:

- `swe_promoted_cmp_v071r1_C3`
- `swe_promoted_cmp_v071r1_C6`
- `swe_promoted_cmp_v071r1_comparison_report.json`

Supporting lane:

- `swe_promoted_support_v071r1_C4`

Probe lane:

- `swe_probe_pytest7373_v071c`
- `swe_probe_flask4992_v071`
- `swe_probe_sphinx8273_v071`

## Reporting Notes

- Cite [BASELINE_0_7_1.md](./BASELINE_0_7_1.md) as the accepted closure baseline for this cycle.
- `C4` remains a supporting lane only.
- The formal promoted SWE lane remains fixed at `8` tasks / `5` repos.
- Do not cite `0.7.1` as a throughput improvement release; cite it as a noise-reduction and attribution-cleanup release.
