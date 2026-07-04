import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

CrawlProgressStatus = Literal["started", "succeeded", "failed"]


@dataclass(frozen=True)
class CrawlProgressEvent:
    url: str
    status: CrawlProgressStatus
    label: str = "crawl"
    duration: float | None = None
    error: str | None = None


CrawlProgressCallback = Callable[[CrawlProgressEvent], None | Awaitable[None]]


async def emit_crawl_progress(
    progress_callback: CrawlProgressCallback | None,
    event: CrawlProgressEvent,
) -> None:
    if progress_callback is None:
        return

    result = progress_callback(event)
    if inspect.isawaitable(result):
        await result
