"""TrajectoryStore: JSONL-based storage for agent step-by-step trajectories.

Each Trajectory represents one complete agent run (task → result), consisting
of multiple Steps. Stored as JSONL — one JSON object per trajectory per line.

Usage
-----
    store = TrajectoryStore(cfg.eval.trajectory_dir)
    traj_id = store.start_trajectory(task_id="he_001", experiment_id="C3", config={"correction": True})
    store.record_step(traj_id, step)
    store.finish_trajectory(traj_id, final_status="success", partial_score=1.0, total_tokens=1500, duration=12.3)
"""

import json
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


@dataclass
class Step:
    step_id: int
    thought: str
    action: dict[str, Any] | None       # {"tool": str, "args": dict} or None
    observation: str
    timestamp: float
    token_count: int = 0
    error_type: str | None = None        # SyntaxError / ImportError / AssertionError / TimeoutError / LogicError / None
    is_retry: bool = False


@dataclass
class Trajectory:
    task_id: str
    experiment_id: str
    config: dict[str, Any]
    steps: list[Step] = field(default_factory=list)
    final_status: str = "running"        # running / success / failed / timeout
    termination_reason: str | None = None
    partial_score: float = 0.0
    total_tokens: int = 0
    duration: float = 0.0
    git_commit: str = field(default_factory=_git_commit)
    random_seed: int = 42
    started_at: float = field(default_factory=time.time)


class TrajectoryStore:
    """Append-only JSONL store for Trajectory objects.

    One JSONL file per experiment_id:  {trajectory_dir}/{experiment_id}.jsonl
    Each line = one completed Trajectory serialised to JSON.
    """

    def __init__(self, trajectory_dir: Path):
        self.trajectory_dir = Path(trajectory_dir)
        self.trajectory_dir.mkdir(parents=True, exist_ok=True)
        self._active: dict[str, Trajectory] = {}  # traj_id -> Trajectory

    def start_trajectory(
        self,
        task_id: str,
        experiment_id: str,
        config: dict[str, Any],
        random_seed: int = 42,
    ) -> str:
        """Begin recording a new trajectory. Returns a unique traj_id."""
        traj_id = f"{experiment_id}_{task_id}_{int(time.time())}"
        self._active[traj_id] = Trajectory(
            task_id=task_id,
            experiment_id=experiment_id,
            config=config,
            random_seed=random_seed,
        )
        return traj_id

    def record_step(self, traj_id: str, step: Step) -> None:
        """Append a step to an in-progress trajectory."""
        if traj_id in self._active:
            self._active[traj_id].steps.append(step)

    def finish_trajectory(
        self,
        traj_id: str,
        final_status: str,
        termination_reason: str | None = None,
        partial_score: float = 0.0,
        total_tokens: int = 0,
        duration: float | None = None,
    ) -> None:
        """Finalise trajectory and flush to JSONL file."""
        traj = self._active.pop(traj_id, None)
        if traj is None:
            return
        traj.final_status = final_status
        traj.termination_reason = termination_reason
        traj.partial_score = partial_score
        traj.total_tokens = total_tokens
        traj.duration = duration if duration is not None else time.time() - traj.started_at

        out_path = self.trajectory_dir / f"{traj.experiment_id}.jsonl"
        with out_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(traj), ensure_ascii=False) + "\n")

    def load(self, experiment_id: str) -> list[dict[str, Any]]:
        """Load all trajectories for a given experiment_id."""
        path = self.trajectory_dir / f"{experiment_id}.jsonl"
        if not path.exists():
            return []
        results = []
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        results.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return results

    def load_all(self) -> list[dict[str, Any]]:
        """Load all trajectories from all JSONL files in the directory."""
        all_trajs = []
        for path in sorted(self.trajectory_dir.glob("*.jsonl")):
            experiment_id = path.stem
            for traj in self.load(experiment_id):
                all_trajs.append(traj)
        return all_trajs
