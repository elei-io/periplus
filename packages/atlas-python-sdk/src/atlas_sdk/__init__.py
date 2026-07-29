"""Atlas Python SDK."""

from . import conn, crawls, errors
from ._config import configure
from .crawls import Crawl, CrawlPage, CrawlStatus, RelationScope


async def aclose() -> None:
    """Close process-owned SDK resources.

    Version 0.1 opens request-scoped HTTP clients, so this is intentionally
    idempotent and currently has no persistent transport to close.
    """


__all__ = [
    "Crawl",
    "CrawlPage",
    "CrawlStatus",
    "RelationScope",
    "aclose",
    "configure",
    "conn",
    "crawls",
    "errors",
]
