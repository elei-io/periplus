import os
import shutil
import time
from pathlib import Path
from uuid import UUID

_DEFAULT_ARTIFACTS_ROOT = Path(__file__).resolve().parents[2] / ".artifacts"


def artifacts_root() -> Path:
    return Path(os.getenv("ARTIFACTS_ROOT", str(_DEFAULT_ARTIFACTS_ROOT))).expanduser()


def task_run_artifacts_dir(task_run_id: UUID | str, root: Path | None = None) -> Path:
    return (root or artifacts_root()) / "task-runs" / str(task_run_id)


def cleanup_artifacts(ttl_seconds: int, root: Path | None = None) -> int:
    if ttl_seconds <= 0:
        return 0

    root = root or artifacts_root()
    if not root.is_dir():
        return 0

    cutoff = time.time() - ttl_seconds
    removed = 0
    for path in root.glob("task-runs/*"):
        if not path.is_dir():
            continue

        try:
            if path.stat().st_mtime >= cutoff:
                continue

            shutil.rmtree(path)
        except OSError:
            continue

        removed += 1

    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass

    return removed
