import shutil
from pathlib import Path


def prepare_workspace(task_setup_files: list[str], workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    for child in workspace.iterdir():
        if child.name == ".gitkeep":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()

    setup_dir = Path(__file__).parent / "benchmarks" / "custom" / "setup_files"
    for filename in task_setup_files:
        src = setup_dir / filename
        dst = workspace / filename
        if not src.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
