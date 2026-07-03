import os
import shutil
import time
from pathlib import Path
from urllib.parse import urlparse

_DEFAULT_CACHE_ROOT = Path(__file__).resolve().parents[2] / ".cache"


def cache_root() -> Path:
    return Path(os.getenv("CACHE_ROOT", str(_DEFAULT_CACHE_ROOT))).expanduser()


def cache_domain(url: str) -> str:
    parsed_url = urlparse(url)
    domain = parsed_url.netloc or "unknown"
    return "".join(char if char.isalnum() or char in {"-", "."} else "-" for char in domain)


def service_cache_root(domain: str, service_name: str) -> Path:
    safe_service = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in service_name)
    return cache_root() / domain / safe_service


def cleanup_cache(ttl_seconds: int, root: Path | None = None) -> int:
    if ttl_seconds <= 0:
        return 0

    root = root or cache_root()
    if not root.is_dir():
        return 0

    cutoff = time.time() - ttl_seconds
    removed = 0
    for path in root.glob("*/*/*"):
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
