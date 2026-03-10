import re

from coder_agent.config import cfg


_ERROR_GUIDANCE = {
    "SyntaxError": "There is a syntax error. Rewrite the specific function or block with the error - check brackets, indentation, and colons.",
    "ImportError": "An import failed. Inspect the traceback, the missing module name, and the file that triggered the import before deciding whether to edit code or install a package.",
    "AssertionError": "An assertion failed. Read the test file to understand expected behavior, then fix the implementation logic.",
    "TimeoutError": "The code timed out. Reconsider the algorithm complexity - look for an O(n log n) or better approach.",
    "LogicError": "There is a logic error. Add debug print statements to trace variable values, analyze the traceback carefully, then fix the root cause.",
}


def classify_error(stderr: str) -> str | None:
    if not stderr.strip():
        return None
    if "SyntaxError" in stderr:
        return "SyntaxError"
    if "ImportError" in stderr or "ModuleNotFoundError" in stderr:
        return "ImportError"
    if "AssertionError" in stderr:
        return "AssertionError"
    if "TimeoutError" in stderr or "timed out" in stderr.lower():
        return "TimeoutError"
    if "Traceback" in stderr or "Error" in stderr:
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
    if error_type == "ImportError":
        return build_import_error_guidance(stderr_text, repeated=repeated)
    return _ERROR_GUIDANCE.get(error_type, "")
