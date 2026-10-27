# Tool Policy Report — v0.8.0-tool-policy (WP2, first slice)

Status: locally implemented and verified offline; not released. The default
policy mode is still `legacy` (see "Default and remaining work").

## Findings that motivated the change

- **Provider keys reached every Agent shell command.** `config.py` calls
  `load_dotenv(override=True)`, which puts `.env` keys such as
  `LLM_GLM_5_API_KEY` into `os.environ`. `workspace_command_env` then passed
  `dict(os.environ)` to every model-generated command. One injected `env`,
  `printenv`, or `python -c "…os.environ…"` would expose them. The MCP adapter
  already used an env allowlist; the shell did not.
- **The only command check was a 7-entry substring denylist.** It misses
  `rm  -rf /` (two spaces), `cd / && rm -rf etc`, `$(echo rm) -rf /`, every
  secret read, every network call and every write outside the workspace.
- **Retrieved documents and MCP output entered the context unmarked.**

## What changed

- `coder_agent/tools/policy.py` (new): `ToolPolicy` evaluates every tool call
  before it runs.

  **Modes:**
  - `legacy`: the old denylist only; the comparison baseline.
  - `workspace-write`: structural rules.
  - `read-only`: workspace-write plus no edits and a read-only command
    allowlist.

  **Shell analysis.** Commands are tokenized with `shlex`, split on control
  operators and redirections, and tracked through `cd`. Wrappers (`env`,
  `timeout`, `nohup`, …), shell keywords and `sh -c` / `find -exec` bodies are
  unwrapped. Every segment is checked, and the most severe decision wins, so
  approving an `ask` step cannot carry a denied step (`curl … | sh`).

  **Rule families:**
  - privilege escalation;
  - destructive system programs;
  - pipe-to-interpreter;
  - writes, redirections, `find -delete` and `rm -r` outside the workspace or
    on unresolvable targets (variables, `~`, unknown cwd);
  - workspace wipe;
  - task-protected paths (globs, e.g. the tests a task forbids editing);
  - `.git` internals;
  - secret paths (`.env`, SSH and cloud credentials; `.env.example` is
    exempt) and secret-variable reads;
  - network egress;
  - package installs from URLs;
  - opaque constructs: `$(…)`, backticks, `eval`, `source`, variable program
    names, function definitions;
  - reads outside the workspace.

  **Decisions.** Each is `allow`, `ask` or `deny`. Without an approver, `ask`
  becomes `deny`, so evaluation and service runs fail closed.

  **Taint.** Output from `knowledge_retrieval` and `mcp.*` is wrapped as
  `[UNTRUSTED CONTENT …]`. After that, network calls, inline interpreter code
  and package installs escalate. Taint is re-derived from the conversation
  history, so it survives an exact resume.
- `coder_agent/tools/execute.py`: the policy runs before install-budget
  reservation. A denied call returns `Error: blocked by tool policy [rule]`, and
  the audit trail records `error_kind = policy_denied:<rule>`. In `legacy` mode
  the path is unchanged.
- `coder_agent/core/workspace_env.py`, `config.py`, `config.yaml`:
  - `tools.scrub_secret_env` (default true) removes secret-named variables
    from Agent command environments.
  - `tools.policy_mode` (default `legacy`; env `CODER_TOOL_POLICY`; per-run
    experiment key `policy_mode`) selects the policy.
- `coder_agent/core/agent.py`: one policy per Agent, reset per run, passed to
  tool execution only when it enforces.
- `coder_agent/eval/security_bench.py` and
  `coder_agent/eval/benchmarks/security/agentsecbench_v1.json` (new):
  AgentSecBench L1.
- `tests/test_tool_policy.py` (new, 28 tests).

## AgentSecBench L1 results

The benchmark has 80 attack cases in 8 categories and 40 benign developer
calls, each one tool call. They run in a fixture workspace containing a
`.env`, a symlink escaping the workspace, and task-protected tests. There is
no approver and no model; results are deterministic. The artifact is
`artifacts/security/agentsecbench-l1-latest.json`; its `cases_sha256` pins the
case file.

| Mode | Attacks stopped | Benign calls blocked |
|---|---:|---:|
| `legacy` (pre-change: denylist, no env scrub) | 10/80 (12.5%) | 0/40 |
| `workspace-write` | 76/80 (95.0%) | 2/40 (5.0%) |
| `read-only` | 80/80 (100%) | 24/40 (blocks edits by design) |

`workspace-write` by category: destructive 16/16, obfuscation 13/14, path
escape 10/10, secret access 12/12, exfiltration 9/10, indirect injection 6/8,
privilege 4/4, eval tampering 6/6.

**Misses**, all limits of static argument analysis:

- inline Python that deletes files or calls the network when no untrusted
  content has been read (`obfuscation-12`, `exfil-07`);
- running an existing workspace script after reading a poisoned document
  (`injection-04`);
- writing a CI workflow file after reading untrusted content (`injection-07`).

Containing code that an allowed interpreter runs needs a sandboxed execution
backend: no network, non-root, read-only outside the workspace. That backend
is planned to share the container work of WP1.

**False positives:** a backtick inside a commit message, and `$(date)` in an
`echo`. Both are command substitutions that the policy refuses to analyze.

**What this does and does not show.** The cases were written by the same author
and at the same time as the policy. They are a regression benchmark for
known attack shapes, not an independent estimate of real-world protection.
They also do not measure how often a model *attempts* these calls. The next
slice adds:

- held-out cases written after the policy was frozen;
- an end-to-end lane in which `glm_5` runs tasks with injected documents and
  canary detection.

## Default and remaining work

`workspace-write` is not yet the default. Flipping it changes Agent behavior
on benchmark tasks, for example a denied `curl` or `$(…)`. The plan is to
measure the utility cost first: the Custom-40 pass rate with
`policy_mode=workspace-write` versus `legacy`, same model and seeds. Flip the
default only if that cost is acceptable, then rebaseline.

Remaining WP2 work:

- the utility measurement;
- the end-to-end injection lane;
- held-out cases;
- an interactive CLI approver for `ask`;
- passing per-task protected globs from SWE tasks without test-edit
  authorization;
- the sandboxed backend.

## Rebaseline decision

**Policy.** With the default `legacy` mode, tool decisions and messages are
unchanged.

**Env scrubbing.** It is on by default and changes the environment of shell
commands. Under this repository's rule (`tools/` changes), C3/C4/C6 benchmark
claims need a rebaseline. No accepted lane is known to depend on
secret-named variables, and the pending WP1 runs re-baseline anyway.

## Verification

- `tests/test_tool_policy.py`: 28 passed. It covers structural denials,
  ordinary commands that must stay allowed, most-severe-segment with an
  approver, history-derived taint, legacy parity, secret paths, env
  scrubbing (provider keys absent from the shell environment), tool-execution
  denial before side effects with untrusted-output wrapping, an Agent run
  whose denial reaches the audit trail, and the L1 regression guard.
- Agent owner suite: 378 passed.
