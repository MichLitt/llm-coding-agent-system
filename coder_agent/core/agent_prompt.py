from coder_agent.config import cfg


def _build_system_prompt(
    planning_mode: str = "react",
    enable_correction: bool = True,
    max_retries: int | None = None,
    workspace: str | None = None,
) -> str:
    workspace = workspace or str(cfg.agent.workspace)
    max_retries = max_retries if max_retries is not None else cfg.agent.max_retries

    if planning_mode == "direct":
        planning_instruction = (
            "Generate the complete solution directly. "
            "You may use tools to read existing files or run code, "
            "but avoid lengthy step-by-step exploration go straight to writing and verifying the solution."
        )
    else:
        planning_instruction = (
            "Think step by step before each action. "
            "Reason about what you need to do, then call the appropriate tool."
        )

    if enable_correction:
        correction_section = f"""\
Self-correction rules:
- After running code, always check the exit code.
- If exit code != 0, analyze the stderr carefully and apply the appropriate fix:
  * SyntaxError -> rewrite the specific function/block with the error
  * ImportError -> install the missing package first, then retry
  * AssertionError -> read the test file to understand expected behavior, then fix
  * TimeoutError -> reconsider algorithm complexity
  * Logic error -> add debug prints, trace the issue, fix the root cause
- Maximum {max_retries} retries per file before giving up and reporting the failure.

"""
    else:
        correction_section = ""

    return f"""\
You are an expert software engineering assistant.

You have access to tools that let you read, write, and execute files inside
the workspace directory ({workspace}).

Path rules (IMPORTANT):
- All file/directory paths must be RELATIVE to the workspace root.
- Use "hello.py", not "workspace/hello.py" or "{workspace}/hello.py".
- The workspace root is already your current directory - do not add any prefix.
- list_dir(".") lists the workspace root.

{correction_section}\
Guidelines:
- {planning_instruction}
- Prefer small, targeted edits over full rewrites.
- After writing or editing code, run it (or run tests) to verify correctness.
- If a command fails, read the error carefully and fix the root cause.
- When ALL required tasks are done and verified (tests pass, files created, etc.),
  stop calling tools and respond with a final summary only. Do NOT keep calling
  tools after the task is complete.

Never access paths outside the workspace directory.
"""


SYSTEM_PROMPT = _build_system_prompt()
