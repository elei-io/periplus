import os
import shutil
import time
from pathlib import Path

_DEFAULT_CACHE_ROOT = Path(__file__).resolve().parents[3] / ".cache" / "scrape"


def cache_root() -> Path:
    return Path(os.getenv("CACHE_ROOT", str(_DEFAULT_CACHE_ROOT))).expanduser()


def cleanup_cache(ttl_seconds: int, root: Path | None = None) -> int:
    if ttl_seconds <= 0:
        return 0

    root = root or cache_root()
    if not root.is_dir():
        return 0

    cutoff = time.time() - ttl_seconds
    removed = 0
    for path in root.iterdir():
        if not path.is_dir():
            continue

        try:
            if path.stat().st_mtime >= cutoff:
                continue

            shutil.rmtree(path)
        except OSError:
            continue

        removed += 1

    return removed
