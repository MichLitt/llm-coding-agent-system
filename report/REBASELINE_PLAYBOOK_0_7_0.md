# REBASELINE_PLAYBOOK_0_7_0

Status: draft workstream playbook for the `0.7.0` cycle.

## Scope

- `patch_file` editing path and prompt preference
- verification-specific recovery constraints
- task-scoped ad hoc install budget
- layered `analysis_report.json` output
- expanded fixed SWE smoke subset, promoted compare subset, and richer SWE task metadata

## Required Local Gates

```bash
uv run pytest
uv run python -m coder_agent --help
uv run python -m coder_agent eval --help
uv run python -m coder_agent analyze <experiment_id>
```

## Planned Artifact Families

- Custom targeted compare: `C3`, `C4`, `C6`
- Custom supporting compare: `C4 similarity`
- SWE smoke rerun on the fixed 3-task smoke subset
- SWE promoted compare rerun for `C3` vs `C6` on the fixed 8-task / 5-repo promoted subset
- Layered analysis reports: `results/<experiment_id>_analysis_report.json`

## Reporting Notes

- Cite `project.version = 0.7.0` as the current code line.
- Continue citing `BASELINE_0_6_0.md` as the accepted baseline until `BASELINE_0_7_0.md` is produced and accepted.
- The checked-in SWE source snapshot and generated manifest now both carry the same fixed 8 promoted task ids; treat source/manifest mismatch as a release blocker.
- Promoted SWE task curation should use explicit `authorized_test_edit_paths` when regression-file edits are intentionally allowed, rather than relying on generic loader fallback.
