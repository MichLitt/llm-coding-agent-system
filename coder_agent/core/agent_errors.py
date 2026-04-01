import re

from coder_agent.config import cfg


_ERROR_GUIDANCE = {
    "SyntaxError": "There is a syntax error. Rewrite the specific function or block with the error - check brackets, indentation, and colons.",
    "ImportError": "An import failed. Inspect the traceback, the missing module name, and the file that triggered the import before deciding whether to edit code or install a package.",
    "AssertionError": "An assertion failed. Read the failing test output, confirm the expected behavior, then fix the implementation logic without changing the API unnecessarily.",
    "TimeoutError": "The code timed out. Reconsider the algorithm complexity - look for an O(n log n) or better approach.",
    "LogicError": "There is a logic or workflow error. Read the first failure block carefully, then fix the root cause before making broad rewrites.",
    "ToolError": "A tool call failed. Re-check tool arguments, relative paths, line ranges, and edit anchors before retrying. If an edit target was not found, read the file again and issue a more precise edit.",
}


def _looks_like_pytest_collection_failure(text: str) -> bool:
    lower = text.lower()
    return "error collecting" in lower or "collected 0 items" in lower


def _looks_like_api_contract_mismatch(text: str) -> bool:
    lower = text.lower()
    return any(
        pattern in lower
        for pattern in (
            "typeerror:",
            "positional argument but",
            "positional arguments but",
            "required positional argument",
            "unexpected keyword argument",
            "got multiple values for argument",
        )
    )


def classify_error(text: str) -> str | None:
    if not text.strip():
        return None
    lower = text.lower()
    if "syntaxerror" in lower or "indentationerror" in lower:
        return "SyntaxError"
    if "importerror" in lower or "modulenotfounderror" in lower:
        return "ImportError"
    if "assertionerror" in lower or " failed" in lower or "\nfailed" in lower:
        return "AssertionError"
    if "timeouterror" in lower or "timeoutexpired" in lower or "timed out" in lower:
        return "TimeoutError"
    if "old_text not found" in lower or "path escapes workspace" in lower:
        return "ToolError"
    if _looks_like_api_contract_mismatch(text):
        return "LogicError"
    if _looks_like_pytest_collection_failure(text):
        return "LogicError"
    error_signals = sum([
        "traceback" in lower,
        "error:" in lower,
        " failed" in lower or "\nfailed" in lower,
    ])
    if "traceback" in lower or error_signals >= 2:
        return "LogicError"
    return None


def extract_exit_code(content: str) -> int | None:
    match = re.search(r"^Exit code:\s*(-?\d+)", content, flags=re.MULTILINE)
    if match is None:
        return None
    return int(match.group(1))


def extract_stderr(content: str) -> str:
    if "STDERR:" not in content:
        return ""
    return content.split("STDERR:", maxsplit=1)[-1].strip()


def extract_stdout(content: str) -> str:
    if "STDOUT:" not in content:
        return ""
    stdout_text = content.split("STDOUT:", maxsplit=1)[-1]
    if "STDERR:" in stdout_text:
        stdout_text = stdout_text.split("STDERR:", maxsplit=1)[0]
    return stdout_text.strip()


def extract_combined_failure_text(content: str) -> str:
    stderr = extract_stderr(content)
    stdout = extract_stdout(content)
    parts = [part for part in (stderr, stdout) if part]
    return "\n".join(parts)


def extract_failure_excerpt(text: str, *, max_lines: int = 8) -> str:
    lines = text.splitlines()
    if not lines:
        return ""

    for index, line in enumerate(lines):
        if line.startswith("E       ") or line.startswith(">       "):
            start = max(0, index - 2)
            end = min(len(lines), index + max_lines)
            excerpt = "\n".join(lines[start:end]).strip()
            if excerpt:
                return excerpt

    for index, line in enumerate(lines):
        if "ERROR collecting" in line or line.strip().startswith("FAILED "):
            end = min(len(lines), index + max_lines)
            excerpt = "\n".join(lines[index:end]).strip()
            if excerpt:
                return excerpt

    tail = [line for line in lines if line.strip()][-max_lines:]
    return "\n".join(tail).strip()


def extract_import_error_details(stderr: str) -> dict[str, str | None]:
    module_name = None
    source_file = None

    module_match = re.search(r"No module named ['\"]([^'\"]+)['\"]", stderr)
    if module_match:
        module_name = module_match.group(1)
    else:
        import_match = re.search(r"cannot import name ['\"]([^'\"]+)['\"]", stderr)
        if import_match:
            module_name = import_match.group(1)

    file_match = re.search(r'File "([^"]+)"', stderr)
    if file_match:
        source_file = file_match.group(1)

    return {"module_name": module_name, "source_file": source_file}


def build_import_error_guidance(stderr_text: str, *, repeated: bool = False) -> str:
    details = extract_import_error_details(stderr_text)
    module_name = details.get("module_name")
    source_file = details.get("source_file")
    workspace = cfg.agent.workspace

    local_candidates: list[str] = []
    if module_name:
        module_path = module_name.replace(".", "/")
        candidate_patterns = [
            f"{module_path}.py",
            f"{module_path}/__init__.py",
            f"**/{module_path}.py",
            f"**/{module_path}/__init__.py",
        ]
        for pattern in candidate_patterns:
            local_candidates.extend(
                path.relative_to(workspace).as_posix()
                for path in workspace.glob(pattern)
            )

    if local_candidates:
        hint = (
            f"ImportError detected for `{module_name}`"
            f"{' from `' + source_file + '`' if source_file else ''}. "
            "This looks like a project-local import or file-path issue. "
            "Check the import statement, module path, package layout, and file names before installing anything. "
            f"Local candidate(s): {', '.join(sorted(set(local_candidates))[:3])}."
        )
    elif module_name and "." not in module_name and module_name.isidentifier():
        hint = (
            f"ImportError detected for `{module_name}`"
            f"{' from `' + source_file + '`' if source_file else ''}. "
            "First inspect the import statement and the triggering file. "
            "Only try `pip install` if the module is clearly third-party and there is no workspace implementation to fix."
        )
    else:
        hint = (
            "An import failed. Inspect the traceback, the missing module name, and the file that triggered the import. "
            "Prefer fixing project-local imports or file names before attempting installation."
        )

    if repeated:
        hint += " This same ImportError repeated. Do not repeat the previous fix blindly; re-read the traceback and verify the exact import path."

    return hint


def build_error_guidance(
    error_type: str | None,
    stderr_text: str,
    *,
    repeated: bool = False,
) -> str:
    if _looks_like_pytest_collection_failure(stderr_text):
        hint = (
            "Pytest could not collect or run the tests. Read the first collection failure block "
            "and fix syntax, import, or test-discovery issues before changing implementation logic. "
            "If you created both implementation and tests, change one side at a time."
        )
        if repeated:
            hint += " This same collection failure repeated. Re-read the exact failure location before editing again."
        return hint
    if _looks_like_api_contract_mismatch(stderr_text):
        hint = (
            "The failure looks like an API or function-signature mismatch. Read the failing call site "
            "and the current implementation, choose one minimal public API, and keep it stable across retries. "
            "Update only one side first unless the traceback proves both files are wrong."
        )
        if repeated:
            hint += " This same API mismatch repeated. Stop rewriting both sides and fix the specific call signature."
        return hint
    if error_type == "ImportError":
        return build_import_error_guidance(stderr_text, repeated=repeated)
    hint = _ERROR_GUIDANCE.get(error_type, "")
    if error_type == "AssertionError" and hint:
        hint += " Avoid brittle whitespace-only assertions unless the task explicitly requires exact formatting. If you wrote both tests and code, prefer keeping one stable API and fixing the implementation first."
    if repeated and hint and error_type != "ImportError":
        hint += " This same failure repeated. Re-read the current file and failure output before trying another broad rewrite."
    return hint
