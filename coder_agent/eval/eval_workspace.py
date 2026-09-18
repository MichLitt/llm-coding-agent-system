import shutil
from pathlib import Path


def prepare_workspace(task_setup_files: list[str], workspace: Path, *, setup_dir: Path | None = None) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    for child in workspace.iterdir():
        if child.name == ".gitkeep":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()

    setup_dir = (setup_dir or (Path(__file__).parent / "benchmarks" / "custom" / "setup_files")).resolve()
    for filename in task_setup_files:
        src = (setup_dir / filename).resolve()
        if setup_dir not in src.parents:
            raise ValueError(f"setup file escapes fixture root: {filename}")
        dst = workspace / filename
        if not src.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
