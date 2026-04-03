"""MBPP (Mostly Basic Python Programming) benchmark integration.

Uses the sanitized split of the MBPP dataset from Hugging Face.
Each task has a natural-language description and 3–5 assert-based tests.

Differences from HumanEval:
- Shorter, more everyday Python problems (strings, math, lists, basic algorithms)
- Tests are plain assert statements (not a check() function)
- 374 tasks in the sanitized split vs 164 in HumanEval
"""

import json
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from coder_agent.eval.runner import TaskSpec

_CACHE_PATH = Path(__file__).parent / "mbpp_data.jsonl"


@dataclass
class MBPPTask:
    task_id: str          # e.g. "mbpp_2"
    text: str             # natural language description (shown to agent)
    code: str             # canonical solution (NOT shown to agent)
    test_list: list[str] = field(default_factory=list)   # assert statements
    test_setup_code: str = ""
    difficulty: str = "medium"


class MBPPBenchmark:
    """Load and evaluate MBPP tasks.

    Data source: HuggingFace `datasets` library, `mbpp` / `sanitized` split.
    On first use the data is downloaded and cached as a local JSONL file.
    """

    def __init__(self, data_path: Path = _CACHE_PATH):
        self.data_path = data_path

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self, limit: int = 0) -> list[MBPPTask]:
        """Return MBPPTask list.  Downloads + caches on first call."""
        if not self.data_path.exists():
            self._download()

        tasks: list[MBPPTask] = []
        with self.data_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                raw = json.loads(line)
                tasks.append(
                    MBPPTask(
                        task_id=f"mbpp_{raw['task_id']}",
                        text=raw["text"],
                        code=raw.get("code", ""),
                        test_list=raw.get("test_list", []),
                        test_setup_code=raw.get("test_setup_code", ""),
                    )
                )
        if limit > 0:
            tasks = tasks[:limit]
        return tasks

    def _download(self) -> None:
        """Download the sanitized MBPP split via the HuggingFace datasets library."""
        self.data_path.parent.mkdir(parents=True, exist_ok=True)
        print("Downloading MBPP dataset from Hugging Face (requires 'datasets' package)…")
        try:
            from datasets import load_dataset  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "The 'datasets' package is required to download MBPP. "
                "Install it with: pip install datasets"
            ) from exc

        ds = load_dataset("mbpp", "sanitized", split="test")
        lines = []
        for item in ds:
            # datasets>=3.x uses "prompt"; older versions used "text"
            text = item.get("prompt") or item.get("text", "")
            # datasets>=3.x uses "test_imports" list; older had "test_setup_code" string
            raw_imports = item.get("test_imports") or item.get("test_setup_code") or []
            if isinstance(raw_imports, list):
                test_setup_code = "\n".join(raw_imports)
            else:
                test_setup_code = str(raw_imports)
            lines.append(json.dumps({
                "task_id": item["task_id"],
                "text": text,
                "code": item.get("code", ""),
                "test_list": item.get("test_list", []),
                "test_setup_code": test_setup_code,
            }))
        self.data_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"Saved {len(lines)} MBPP tasks to {self.data_path}")

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def evaluate_solution(self, task: MBPPTask, solution: str) -> bool:
        """Run assert-based tests against the solution string.

        Returns True if all assertions pass, False otherwise.
        """
        if not solution.strip():
            return False

        test_code = "\n".join(task.test_list)
        combined = "\n\n".join(filter(None, [
            task.test_setup_code.strip(),
            solution.strip(),
            test_code.strip(),
        ])) + "\n"

        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, encoding="utf-8"
            ) as fh:
                fh.write(combined)
                temp_path = Path(fh.name)

            result = subprocess.run(
                [sys.executable, str(temp_path)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            return False
        except Exception:
            return False
        finally:
            try:
                temp_path.unlink()
            except (OSError, UnboundLocalError):
                pass

    def evaluate_solution_from_metadata(self, metadata: dict, solution: str) -> bool:
        """Convenience wrapper used by the verification hook (passes metadata dict)."""
        task = MBPPTask(
            task_id=metadata.get("task_id", ""),
            text=metadata.get("text", ""),
            code="",
            test_list=metadata.get("test_list", []),
            test_setup_code=metadata.get("test_setup_code", ""),
        )
        return self.evaluate_solution(task, solution)

    # ------------------------------------------------------------------
    # Agent prompt / TaskSpec
    # ------------------------------------------------------------------

    def build_agent_prompt(self, task: MBPPTask) -> str:
        tests_str = "\n".join(task.test_list)
        return (
            f"{task.text}\n\n"
            "Write your complete Python solution to `solution.py` in the workspace.\n\n"
            "Your solution must pass all of the following assertions:\n"
            f"```python\n{tests_str}\n```\n\n"
            "Required workflow:\n"
            "1. Write the implementation to solution.py.\n"
            "2. Run `python solution.py` once to check for syntax errors.\n"
            "3. If that succeeds, stop and provide a short summary."
        )

    def to_task_spec(self, task: MBPPTask) -> TaskSpec:
        """Convert an MBPPTask into a TaskSpec suitable for EvalRunner."""
        return TaskSpec(
            task_id=task.task_id,
            description=self.build_agent_prompt(task),
            difficulty=task.difficulty,
            setup_files=[],
            verification=[],
            verification_contract={"mode": "mbpp_official", "max_attempts": 2},
            max_steps=10,
            metadata={
                "benchmark": "mbpp",
                "task_id": task.task_id,
                "text": task.text,
                "test_list": task.test_list,
                "test_setup_code": task.test_setup_code,
            },
        )

    def extract_solution_from_workspace(self, workspace: Path) -> str:
        """Read solution.py from the agent's workspace."""
        solution_path = workspace / "solution.py"
        if not solution_path.exists():
            return ""
        return solution_path.read_text(encoding="utf-8", errors="replace").strip()
