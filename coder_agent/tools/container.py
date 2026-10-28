"""Run the Agent's tools inside a Docker container (e.g. SWE-bench instance images).

``DockerSandbox`` owns one long-lived container started from a task image and
runs every operation with ``docker exec``. The container tools subclass the
local tools, so names, descriptions and schemas are identical. They return the
same output formats, so the model sees no difference except that the
workspace is the container path (``/testbed``).

Isolation defaults: no network, a pids limit, ``no-new-privileges``, optional
CPU/memory limits, and an in-container ``timeout`` so a timed-out command is
killed in the container rather than only on the host. The host environment
(and its API keys) is never passed to the container.

Paths are confined lexically to the workdir; symlinks inside the container are
not resolved, because the container itself is the boundary.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from coder_agent.tools.base import Tool
from coder_agent.tools.file_tools import ListDirTool, PatchFileTool, ReadFileTool, WriteFileTool
from coder_agent.tools.search_tool import SearchCodeTool, _validate_file_glob
from coder_agent.tools.shell_tool import BLOCKED_PATTERNS, RunCommandTool, _decode_output

Runner = Callable[[list[str], "bytes | None", float], "tuple[int, bytes, bytes]"]

# SWE-bench instance images keep the task environment in a conda env.
SWEBENCH_ENV_SETUP = "source /opt/miniconda3/bin/activate testbed >/dev/null 2>&1 || true"
_TIMEOUT_EXIT = 124
_MISSING_EXIT = 44


class CommandTimeout(RuntimeError):
    pass


def subprocess_runner(argv: list[str], stdin: bytes | None, timeout: float) -> tuple[int, bytes, bytes]:
    try:
        proc = subprocess.run(argv, input=stdin, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise CommandTimeout(f"docker command exceeded {timeout:.0f}s") from exc
    return proc.returncode, proc.stdout, proc.stderr


def swebench_image(instance_id: str, namespace: str = "swebench", arch: str = "x86_64") -> str:
    """Official SWE-bench instance image name (Docker Hub escapes '__' as '_1776_')."""
    return f"{namespace}/sweb.eval.{arch}.{instance_id.replace('__', '_1776_')}:latest".lower()


class DockerSandbox:
    def __init__(
        self,
        container: str,
        *,
        workdir: str = "/testbed",
        env_setup: str = "",
        docker: str = "docker",
        runner: Runner = subprocess_runner,
        timeout_wrapper: bool = True,
        image: str | None = None,
    ) -> None:
        self.container = container
        self.workdir = workdir
        self.env_setup = env_setup
        self.docker = docker
        self.runner = runner
        self.timeout_wrapper = timeout_wrapper
        self.image = image

    @classmethod
    def start(
        cls,
        image: str,
        *,
        name: str | None = None,
        workdir: str = "/testbed",
        env_setup: str = "",
        network: str = "none",
        cpus: float | None = None,
        memory: str | None = None,
        pids_limit: int = 1024,
        docker: str = "docker",
        runner: Runner = subprocess_runner,
        timeout_wrapper: bool = True,
    ) -> "DockerSandbox":
        argv = [
            docker, "run", "-d", "--name", name or f"coder-agent-{uuid.uuid4().hex[:12]}",
            "--network", network, "--pids-limit", str(pids_limit),
            "--security-opt", "no-new-privileges", "-w", workdir,
        ]
        if cpus:
            argv += ["--cpus", str(cpus)]
        if memory:
            argv += ["--memory", memory]
        argv += ["--entrypoint", "sleep", image, "infinity"]
        code, stdout, stderr = runner(argv, None, 600)
        if code != 0:
            raise RuntimeError(f"docker run failed ({code}): {_decode_output(stderr).strip()}")
        return cls(
            _decode_output(stdout).strip(), workdir=workdir, env_setup=env_setup,
            docker=docker, runner=runner, timeout_wrapper=timeout_wrapper, image=image,
        )

    def image_provenance(self) -> dict[str, Any] | None:
        """Local image id and registry digests of the image, for run provenance."""
        if not self.image:
            return None
        code, stdout, _ = self.runner(
            [self.docker, "image", "inspect", "--format", '{{.Id}}|{{join .RepoDigests ","}}', self.image], None, 60
        )
        if code != 0:
            return None
        image_id, _, digests = _decode_output(stdout).strip().partition("|")
        return {"image": self.image, "image_id": image_id, "repo_digests": [d for d in digests.split(",") if d]}

    def exec(self, script: str, *, timeout: int = 30, stdin: bytes | None = None, setup: bool = True) -> tuple[int, bytes, bytes]:
        body = f"{self.env_setup}\n{script}" if setup and self.env_setup else script
        command = f"timeout -k 5 {int(timeout)} bash -c {shlex.quote(body)}" if self.timeout_wrapper else body
        argv = [self.docker, "exec"]
        if stdin is not None:
            argv.append("-i")
        argv += ["-w", self.workdir, self.container, "bash", "-c", command]
        code, stdout, stderr = self.runner(argv, stdin, timeout + 30)
        if self.timeout_wrapper and code == _TIMEOUT_EXIT:
            raise CommandTimeout("command timed out")
        return code, stdout, stderr

    async def aexec(self, script: str, **kwargs: Any) -> tuple[int, bytes, bytes]:
        return await asyncio.to_thread(self.exec, script, **kwargs)

    def path(self, raw: str) -> str | None:
        """Absolute container path confined to the workdir, or None if it escapes."""
        root = PurePosixPath(self.workdir)
        candidate = PurePosixPath(raw) if str(raw).startswith("/") else root / str(raw)
        normalized = PurePosixPath(os.path.normpath(str(candidate)))
        if normalized != root and root not in normalized.parents:
            return None
        return str(normalized)

    def relative(self, absolute: str) -> str:
        return str(PurePosixPath(absolute).relative_to(self.workdir))

    async def read_text(self, absolute: str) -> str | None:
        code, stdout, stderr = await self.aexec(
            f"test -f {shlex.quote(absolute)} || exit {_MISSING_EXIT}; cat -- {shlex.quote(absolute)}", setup=False
        )
        if code == _MISSING_EXIT:
            return None
        if code != 0:
            raise RuntimeError(_decode_output(stderr).strip() or "read failed")
        return stdout.decode("utf-8", errors="replace")

    async def write_text(self, absolute: str, content: str) -> None:
        quoted = shlex.quote(absolute)
        code, _, stderr = await self.aexec(
            f'mkdir -p -- "$(dirname -- {quoted})" && cat > {quoted}', stdin=content.encode("utf-8"), setup=False
        )
        if code != 0:
            raise RuntimeError(_decode_output(stderr).strip() or "write failed")

    async def is_dir(self, absolute: str) -> bool:
        code, _, _ = await self.aexec(f"test -d {shlex.quote(absolute)}", setup=False)
        return code == 0

    async def list_dir(self, absolute: str) -> list[tuple[str, bool]]:
        code, stdout, stderr = await self.aexec(f"ls -1Ap -- {shlex.quote(absolute)}", setup=False)
        if code != 0:
            raise RuntimeError(_decode_output(stderr).strip() or "list failed")
        entries = [line for line in stdout.decode("utf-8", errors="replace").splitlines() if line]
        return [(line.rstrip("/"), line.endswith("/")) for line in entries]

    def diff(self) -> str:
        """Unified diff of every change in the workdir, including new files."""
        code, stdout, stderr = self.exec("git add -A && git diff --cached --binary HEAD", timeout=120, setup=False)
        if code != 0:
            raise RuntimeError(_decode_output(stderr).strip() or "git diff failed")
        return stdout.decode("utf-8", errors="replace")

    def stop(self) -> None:
        self.runner([self.docker, "rm", "-f", self.container], None, 120)


class ContainerReadFileTool(ReadFileTool):
    def __init__(self, sandbox: DockerSandbox):
        super().__init__(Path(sandbox.workdir))
        self.sandbox = sandbox

    async def execute(self, path: str, max_lines: int = 0, start_line: int = 1, min_lines: Any = None) -> str:
        full_path = self.sandbox.path(path)
        if full_path is None:
            return "Error: path escapes workspace"
        try:
            content = await self.sandbox.read_text(full_path)
            if content is None:
                return f"Error: file not found: {path}"
            start_line = self._normalize_start_line(start_line=start_line, min_lines=min_lines)
            if start_line > 1 or max_lines > 0:
                lines = content.splitlines(keepends=True)[start_line - 1:]
                return "".join(lines[:max_lines] if max_lines > 0 else lines)
            return content
        except Exception as e:
            return f"Error: {e}"


class ContainerWriteFileTool(WriteFileTool):
    def __init__(self, sandbox: DockerSandbox):
        super().__init__(Path(sandbox.workdir))
        self.sandbox = sandbox

    async def execute(self, operation: str, path: str, content: str = "", old_text: str = "", new_text: str = "") -> str:
        full_path = self.sandbox.path(path)
        if full_path is None:
            return "Error: path escapes workspace"
        try:
            if operation == "write":
                await self.sandbox.write_text(full_path, content)
                return f"Written: {self.sandbox.relative(full_path)}"
            if operation == "edit":
                current = await self.sandbox.read_text(full_path)
                if current is None:
                    return f"Error: file not found: {PurePosixPath(full_path).name}"
                if not old_text:
                    return "Error: old_text is required for edit"
                if old_text == new_text:
                    return "Error: edit is a no-op: old_text and new_text are identical, so nothing would change"
                if old_text not in current:
                    return "Error: old_text not found in file"
                await self.sandbox.write_text(full_path, current.replace(old_text, new_text, 1))
                return f"Edited: {self.sandbox.relative(full_path)}"
        except Exception as e:
            return f"Error: {e}"
        return f"Error: unknown operation '{operation}'"


class ContainerPatchFileTool(PatchFileTool):
    """Same edit semantics and messages as ``PatchFileTool`` (kept in step by parity tests)."""

    def __init__(self, sandbox: DockerSandbox):
        super().__init__(Path(sandbox.workdir))
        self.sandbox = sandbox

    async def execute(self, path: str, edits: list[dict[str, Any]]) -> str:
        full_path = self.sandbox.path(path)
        if full_path is None:
            return "Error: path escapes workspace"
        try:
            original_content = await self.sandbox.read_text(full_path)
            if original_content is None:
                return f"Error: file not found: {path}"
            if not edits:
                return "Error: edits must contain at least one edit"
            candidate_content = original_content
            edit_reports: list[str] = []
            for index, edit in enumerate(edits, start=1):
                if not isinstance(edit, dict):
                    return f"Error: edit #{index} must be an object"
                old_text = str(edit.get("old_text", ""))
                new_text = str(edit.get("new_text", ""))
                expected = int(edit.get("expected_replacements", 1))
                if not old_text:
                    return f"Error: edit #{index} old_text is required"
                if old_text == new_text:
                    return f"Error: edit #{index} is a no-op: old_text and new_text are identical, so nothing would change"
                if expected < 0:
                    return f"Error: edit #{index} expected_replacements must be >= 0"
                actual_matches = candidate_content.count(old_text)
                edit_reports.append(f"edit #{index}: matches={actual_matches}, expected={expected}")
                if actual_matches != expected:
                    return (
                        f"Error: patch_file edit #{index} match count mismatch for {path} "
                        f"(actual_matches={actual_matches}, expected_replacements={expected}).\n"
                        f"Applied edits before failure: {index - 1}/{len(edits)}\n"
                        + "\n".join(edit_reports)
                    )
                if expected > 0:
                    candidate_content = candidate_content.replace(old_text, new_text, expected)
            await self.sandbox.write_text(full_path, candidate_content)
            return (
                f"Patched: {self.sandbox.relative(full_path)}\n"
                f"Applied edits: {len(edits)}/{len(edits)}\n"
                + "\n".join(edit_reports)
            )
        except Exception as e:
            return f"Error: {e}"


class ContainerListDirTool(ListDirTool):
    def __init__(self, sandbox: DockerSandbox):
        super().__init__(Path(sandbox.workdir))
        self.workspace = Path(sandbox.workdir)
        self.sandbox = sandbox

    async def execute(self, path: str = ".", depth: int = 1) -> str:
        full_path = self.sandbox.path(path)
        if full_path is None:
            return "Error: path escapes workspace"
        if not await self.sandbox.is_dir(full_path):
            return f"Error: directory not found: {path}"
        if depth < 1:
            return "Error: depth must be >= 1"
        root_label = f"[workspace root: {self.sandbox.workdir}]" if path in ("", ".") else path.rstrip("/\\")
        lines = [root_label]
        stack = [(full_path, 0)]
        while stack:
            current_dir, current_depth = stack.pop()
            if current_depth >= depth:
                continue
            try:
                entries = sorted(await self.sandbox.list_dir(current_dir), key=lambda e: (not e[1], e[0]))
            except Exception as e:
                return f"Error: {e}"
            prefix = "  " * (current_depth + 1)
            child_dirs = []
            for name, is_dir in entries:
                lines.append(f"{prefix}{name}{'/' if is_dir else ''}")
                if is_dir:
                    child_dirs.append(str(PurePosixPath(current_dir) / name))
            for child in reversed(child_dirs):
                stack.append((child, current_depth + 1))
        return "\n".join(lines)


class ContainerSearchCodeTool(SearchCodeTool):
    def __init__(self, sandbox: DockerSandbox):
        super().__init__(Path(sandbox.workdir))
        self.sandbox = sandbox

    async def execute(self, pattern: str, path: str = ".", file_glob: str = "*", case_sensitive: bool = True, max_results: int = 50) -> str:
        if max_results < 1:
            return "Error: max_results must be >= 1"
        root = self.sandbox.path(path)
        if root is None:
            return "Error: path escapes workspace"
        file_glob_error = _validate_file_glob(file_glob)
        if file_glob_error:
            return file_glob_error
        rel = self.sandbox.relative(root) or "."
        flags = "-rnIE" + ("" if case_sensitive else "i")
        include = f"--include={shlex.quote(file_glob)} " if file_glob and file_glob != "*" else ""
        script = (
            f"test -e {shlex.quote(root)} || exit {_MISSING_EXIT}; "
            f"grep {flags} {include}-e {shlex.quote(pattern)} -- {shlex.quote(rel)} > /tmp/.coder-agent-search; "
            f"status=$?; head -n {int(max_results)} /tmp/.coder-agent-search; exit $status"
        )
        try:
            code, stdout, stderr = await self.sandbox.aexec(script, setup=False)
        except Exception as e:
            return f"Error: {e}"
        if code == _MISSING_EXIT:
            return f"Error: path not found: {path}"
        if code not in (0, 1):
            return f"Error: {_decode_output(stderr).strip() or 'grep failed'}"
        lines = [line.removeprefix("./") for line in _decode_output(stdout).splitlines()]
        return "\n".join(lines[:max_results]) if lines else "No matches found."


class ContainerRunCommandTool(RunCommandTool):
    def __init__(self, sandbox: DockerSandbox):
        # RunCommandTool.__init__ would create the workspace on the host.
        Tool.__init__(
            self,
            name="run_command",
            description="Run a shell command inside the workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "default": 30},
                },
                "required": ["command"],
            },
        )
        self.workspace = Path(sandbox.workdir)
        self.max_ad_hoc_installs_per_task = None
        self.ad_hoc_install_count = 0
        self.sandbox = sandbox

    async def execute(self, command: str, timeout: int = 30, _install_budget_reserved: bool = False) -> str:
        if any(p in command for p in BLOCKED_PATTERNS):
            raise RuntimeError("command blocked for safety")
        if not _install_budget_reserved:
            budget_error = self.try_reserve_ad_hoc_install(command)
            if budget_error:
                return budget_error
        try:
            code, stdout, stderr = await self.sandbox.aexec(f"set -o pipefail\n{command}", timeout=int(timeout))
        except CommandTimeout as exc:
            raise RuntimeError("command timed out") from exc
        return (
            f"Exit code: {code}\n"
            f"STDOUT:\n{_decode_output(stdout)}\n"
            f"STDERR:\n{_decode_output(stderr)}"
        )


def build_container_tools(sandbox: DockerSandbox) -> list[Tool]:
    """Container counterparts of ``build_tools`` in the same order (no retrieval or MCP)."""
    return [
        ContainerReadFileTool(sandbox),
        ContainerWriteFileTool(sandbox),
        ContainerPatchFileTool(sandbox),
        ContainerListDirTool(sandbox),
        ContainerRunCommandTool(sandbox),
        ContainerSearchCodeTool(sandbox),
    ]
