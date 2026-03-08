"""HumanEval benchmark integration."""

import gzip
import io
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path


_HUMANEVAL_URL = "https://raw.githubusercontent.com/openai/human-eval/master/data/HumanEval.jsonl.gz"
_HUMANEVAL_FALLBACK_URL = (
    "https://raw.githubusercontent.com/openai/human-eval/refs/heads/master/data/HumanEval.jsonl.gz"
)
_CACHE_PATH = Path(__file__).parent / "humaneval_data.jsonl"


@dataclass
class HumanEvalTask:
    task_id: str
    prompt: str
    entry_point: str
    test: str
    canonical_solution: str
    difficulty: str = "medium"


@dataclass
class HumanEvalResult:
    task_id: str
    passed: bool
    error: str | None = None
    duration: float = 0.0
    solution: str = ""


class HumanEvalBenchmark:
    """Load and execute HumanEval tasks."""

    def __init__(self, data_path: Path = _CACHE_PATH):
        self.data_path = data_path

    def load(self, limit: int = 0) -> list[HumanEvalTask]:
        if not self.data_path.exists():
            self._download()

        tasks: list[HumanEvalTask] = []
        with self.data_path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                raw = json.loads(line)
                tasks.append(
                    HumanEvalTask(
                        task_id=raw["task_id"],
                        prompt=raw["prompt"],
                        entry_point=raw["entry_point"],
                        test=raw["test"],
                        canonical_solution=raw.get("canonical_solution", ""),
                    )
                )
        if limit > 0:
            tasks = tasks[:limit]
        return tasks

    def _download(self) -> None:
        self.data_path.parent.mkdir(parents=True, exist_ok=True)
        for url in (_HUMANEVAL_URL, _HUMANEVAL_FALLBACK_URL):
            try:
                print(f"Downloading HumanEval from {url}...")
                with urllib.request.urlopen(url, timeout=30) as response:
                    raw = response.read()
                with gzip.GzipFile(fileobj=io.BytesIO(raw)) as zipped:
                    content = zipped.read().decode("utf-8")
                self.data_path.write_text(content, encoding="utf-8")
                print(f"Saved to {self.data_path}")
                return
            except Exception as exc:
                print(f"Failed ({exc}), trying next...")

        raise RuntimeError(
            "Could not download HumanEval. "
            f"Save the dataset as {self.data_path} and retry."
        )

    def evaluate_solution(self, task: HumanEvalTask, solution: str) -> HumanEvalResult:
        """Run the supplied solution against the task's official tests."""
        if not solution.strip():
            return HumanEvalResult(
                task_id=task.task_id,
                passed=False,
                error="Empty solution",
                solution=solution,
            )

        implementation = solution
        if f"def {task.entry_point}" not in implementation:
            implementation = f"{task.prompt}\n{solution}"

        test_code = "\n\n".join(
            [
                implementation.rstrip(),
                task.test.rstrip(),
                f"check({task.entry_point})",
            ]
        ) + "\n"

        start = time.time()
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            delete=False,
            encoding="utf-8",
        ) as handle:
            handle.write(test_code)
            temp_path = Path(handle.name)

        try:
            result = subprocess.run(
                [sys.executable, str(temp_path)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            passed = result.returncode == 0
            error = None if passed else (result.stderr.strip() or result.stdout.strip())
        except subprocess.TimeoutExpired:
            passed = False
            error = "TimeoutExpired"
        except Exception as exc:
            passed = False
            error = str(exc)
        finally:
            try:
                temp_path.unlink()
            except OSError:
                pass

        return HumanEvalResult(
            task_id=task.task_id,
            passed=passed,
            error=error,
            duration=time.time() - start,
            solution=solution,
        )

    def build_agent_prompt(self, task: HumanEvalTask) -> str:
        return (
            "Complete the following Python function.\n\n"
            f"```python\n{task.prompt}```\n\n"
            "Required workflow:\n"
            "1. Write the complete implementation to `solution.py` in the workspace.\n"
            "2. Run `python solution.py` exactly once to verify it has no syntax errors.\n"
            "3. If that command succeeds, stop immediately and respond with a short final summary.\n"
            "Do not run extra verification commands or call more tools after the syntax check succeeds."
        )

    def extract_solution_from_workspace(self, workspace: Path, entry_point: str) -> str:
        """Read the full `solution.py` content from the workspace.

        HumanEval solutions may depend on imports or helper functions defined
        above the entry point, so the evaluator should preserve the entire file.
        """
        solution_path = workspace / "solution.py"
        if not solution_path.exists():
            return ""
        return solution_path.read_text(encoding="utf-8", errors="replace").strip()
