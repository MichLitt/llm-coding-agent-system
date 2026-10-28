"""Docker sandbox tools: argv construction, isolation flags and parity with the local tools."""

import asyncio
import re
import shutil
import subprocess

import pytest

from coder_agent.core.tool_registry import build_tools
from coder_agent.tools.container import (
    SWEBENCH_CONTAINER_ENV,
    CommandTimeout,
    DockerSandbox,
    build_container_tools,
    swebench_image,
)

FIXTURE = {
    "src/app.py": "def main():\n    return 1\n\n# TODO: handle errors\n",
    "src/util/helpers.py": "VALUE = 2\n",
    "README.md": "# demo\nTODO list\n",
}


def _populate(root):
    for relative, content in FIXTURE.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


class FakeDocker:
    """Executes `docker exec` scripts with local bash, mapping /testbed onto a directory."""

    def __init__(self, root):
        self.root = root
        self.calls = []

    def __call__(self, argv, stdin, timeout):
        self.calls.append(argv)
        if argv[1] == "run":
            return 0, b"cid-1\n", b""
        if argv[1] == "rm":
            return 0, b"", b""
        if argv[1] == "image":
            return 0, b"sha256:feed|swebench/demo@sha256:beef\n", b""
        assert argv[1] == "exec"
        script = argv[-1].replace("/testbed", str(self.root))
        proc = subprocess.run(["bash", "-c", script], input=stdin, capture_output=True, cwd=self.root, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr


@pytest.fixture
def pair(tmp_path):
    local_root, container_root = tmp_path / "local", tmp_path / "container"
    for root in (local_root, container_root):
        root.mkdir()
        _populate(root)
    fake = FakeDocker(container_root)
    sandbox = DockerSandbox("cid-1", runner=fake, timeout_wrapper=False)
    local = {tool.name: tool for tool in build_tools(local_root, enable_knowledge_retrieval=False)}
    container = {tool.name: tool for tool in build_container_tools(sandbox)}
    return local, container, local_root, container_root, fake


def _run(tool, **kwargs):
    return asyncio.run(tool.execute(**kwargs))


def test_container_tools_expose_identical_tool_schemas(pair):
    local, container, *_ = pair
    assert list(container) == list(local)
    for name in local:
        assert container[name].to_dict() == local[name].to_dict()


@pytest.mark.parametrize(
    ("tool", "kwargs"),
    [
        ("read_file", {"path": "src/app.py"}),
        ("read_file", {"path": "src/app.py", "start_line": 2, "max_lines": 2}),
        ("read_file", {"path": "missing.py"}),
        ("read_file", {"path": "../escape.py"}),
        ("write_file", {"operation": "write", "path": "src/new/mod.py", "content": "X = 1\n"}),
        ("write_file", {"operation": "edit", "path": "src/app.py", "old_text": "return 1", "new_text": "return 2"}),
        ("write_file", {"operation": "edit", "path": "src/app.py", "old_text": "absent", "new_text": "x"}),
        ("write_file", {"operation": "edit", "path": "src/app.py", "old_text": "return 1", "new_text": "return 1"}),
        ("patch_file", {"path": "src/app.py", "edits": [{"old_text": "return 1", "new_text": "return 3"}]}),
        ("patch_file", {"path": "src/app.py", "edits": [{"old_text": "def", "new_text": "x", "expected_replacements": 2}]}),
        ("patch_file", {"path": "src/app.py", "edits": [{"old_text": "return 1", "new_text": "return 1"}]}),
        ("list_dir", {"path": "src", "depth": 2}),
        ("list_dir", {"path": "nope"}),
        ("search_code", {"pattern": "VALUE", "path": "src"}),
        ("search_code", {"pattern": "nothing-matches-this"}),
    ],
)
def test_container_tools_match_local_tool_output(pair, tool, kwargs):
    local, container, local_root, container_root, _ = pair
    expected = _run(local[tool], **kwargs)
    if tool == "search_code" and shutil.which("rg") is None:
        # The local tool's pure-Python fallback adds a space after the line
        # number; with ripgrep (its primary path) the format is path:line:text.
        expected = re.sub(r"^(.+?:\d+): ", r"\1:", expected, flags=re.MULTILINE)
    assert _run(container[tool], **kwargs) == expected
    for relative in ("src/app.py", "src/new/mod.py"):
        local_file, container_file = local_root / relative, container_root / relative
        assert local_file.exists() == container_file.exists()
        if local_file.exists():
            assert container_file.read_text() == local_file.read_text()


def test_list_dir_root_label_uses_container_workdir(pair):
    _, container, *_ = pair
    assert _run(container["list_dir"], path=".").splitlines()[0] == "[workspace root: /testbed]"


def test_run_command_format_pipefail_and_env_setup(pair):
    _, container, _, _, fake = pair
    output = _run(container["run_command"], command="false | cat; echo after")
    assert output.startswith("Exit code: 0\nSTDOUT:\nafter")

    failing = _run(container["run_command"], command="python3 -c 'raise SystemExit(3)' | cat")
    assert failing.startswith("Exit code: 3\n")
    assert fake.calls[-1][:3] == ["docker", "exec", "-w"]


def test_sandbox_start_applies_isolation_flags_and_wraps_timeouts():
    calls = []

    def runner(argv, stdin, timeout):
        calls.append(argv)
        return (0, b"cid-9\n", b"") if argv[1] == "run" else (124, b"", b"")

    sandbox = DockerSandbox.start("img:latest", name="n1", cpus=2, memory="4g", env=SWEBENCH_CONTAINER_ENV, runner=runner)
    run_argv = calls[0]
    assert sandbox.container == "cid-9"
    for flag, value in (("--network", "none"), ("--pids-limit", "1024"), ("--security-opt", "no-new-privileges"),
                        ("--cpus", "2"), ("--memory", "4g"), ("--entrypoint", "sleep")):
        assert run_argv[run_argv.index(flag) + 1] == value
    assert [run_argv[i + 1] for i, arg in enumerate(run_argv) if arg == "-e"] == ["LANG=C.UTF-8", "LC_ALL=C.UTF-8"]
    assert run_argv[-2:] == ["img:latest", "infinity"]

    with pytest.raises(CommandTimeout):
        sandbox.exec("sleep 100", timeout=5)
    assert calls[-1][-1].startswith("timeout -k 5 5 bash -c ")


def test_paths_are_confined_and_image_names_follow_swebench_convention():
    sandbox = DockerSandbox("c")
    assert sandbox.path("src/a.py") == "/testbed/src/a.py"
    assert sandbox.path("/testbed/../etc/passwd") is None
    assert sandbox.path("../x") is None
    assert swebench_image("django__django-11099") == "swebench/sweb.eval.x86_64.django_1776_django-11099:latest"


def test_diff_includes_new_files(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    (tmp_path / "a.py").write_text("x = 2\n")
    (tmp_path / "b.py").write_text("y = 1\n")

    patch = DockerSandbox("c", runner=FakeDocker(tmp_path), timeout_wrapper=False).diff()

    assert "diff --git a/a.py b/a.py" in patch and "+x = 2" in patch
    assert "diff --git a/b.py b/b.py" in patch and "new file mode" in patch


def test_noop_edits_are_rejected_and_leave_the_file_unchanged(pair):
    # Pilot finding: a no-op patch reported "Patched", and the model kept
    # re-issuing it believing the fix had landed.
    local, container, local_root, container_root, _ = pair
    before = (container_root / "src/app.py").read_text()
    output = _run(container["patch_file"], path="src/app.py", edits=[{"old_text": "return 1", "new_text": "return 1"}])
    assert output.startswith("Error: edit #1 is a no-op")
    assert (container_root / "src/app.py").read_text() == before


def test_missing_required_tool_arguments_are_named_for_local_and_container_tools(pair):
    from coder_agent.tools.execute import execute_tools

    local, container, *_ = pair
    call = {"id": "c1", "name": "write_file", "input": {"path": "repro.py", "content": "print(1)\n"}}
    for tools in (local, container):
        [result] = asyncio.run(execute_tools([call], tools))
        assert result["is_error"] is True
        assert result["content"] == "Error: write_file is missing required argument(s): operation (required: operation, path)"
