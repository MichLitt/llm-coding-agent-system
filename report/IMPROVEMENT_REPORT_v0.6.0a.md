# Improvement Report v0.6.0a

> Date: 2026-04-07
> Type: behavior/runtime change

## What Changed

Implemented the `v0.6.0a` runtime foundation for eval runs:

- `coder_agent/eval/runner.py`
- `coder_agent/eval/eval_checkpoint.py`
- `coder_agent/cli/eval.py`
- `coder_agent/cli/run_ablation.py`
- `coder_agent/cli/factory.py`
- `coder_agent/core/agent.py`
- `coder_agent/core/agent_run_context.py`
- `coder_agent/core/agent_prompt.py`
- `coder_agent/core/agent_errors.py`
- `coder_agent/core/session.py`
- `coder_agent/core/tool_registry.py`
- `coder_agent/tools/file_tools.py`
- `coder_agent/tools/search_tool.py`
- `coder_agent/tools/shell_tool.py`

Supporting test updates were made in:

- `tests/test_eval_runner.py`
- `tests/test_cli_eval.py`
- `tests/test_file_tools.py`
- `tests/test_search_tool.py`
- `tests/test_shell_tool.py`
- `tests/test_agent_errors.py`

Documentation updates:

- `README.md`
- `report/REBASELINE_PLAYBOOK_0_6_0.md`

## Intended Effect On Agent Behavior

- Each labeled eval run now gets a unique run identity and isolated workspace path.
- Agent file access, shell execution, code search, memory lookup/write, prompt metadata, and import-error guidance now follow the runtime workspace instead of the global configured workspace.
- Run manifests now include explicit run/workspace identity fields.
- `--resume` is now an auditable skip-and-continue mechanism with compatibility checks, not a weak checkpoint merge.

## Public/Interface Changes

- `make_agent(..., workspace: Path | None = None)`
- `build_tools(workspace: Path)`
- `Agent(..., workspace: Path | None = None)`
- `EvalRunner(agent_factory=Callable[[dict, Path], Any], ...)`
- `build_import_error_guidance(..., workspace: Path | None = None)`

Manifest additions:

- `run_id`
- `workspace_path`
- `workspace_mode`
- `task_ids`

## Rebaseline Requirement

Yes. A rebaseline is required.

Reason:

- the change touches `core/agent.py`
- the effective runtime behavior of `tools/` changed
- eval resume semantics and workspace isolation changed materially

Per repository policy, benchmark numbers from `0.5.1` remain historical accepted artifacts until a fresh `0.6.0` artifact set is produced.
