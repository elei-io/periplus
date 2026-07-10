from typing import Annotated

import typer
from pydantic import TypeAdapter
from rich.console import Console
from cli.action_runs import run_action
from cli.cache import cache_input
from cli.progress import ProgressRenderer
from cli.summary import print_task_summary
from tasks.schemas import BoundedTaskOutputJson

console = Console()
_INDEX_ADAPTER = TypeAdapter(BoundedTaskOutputJson)


def index(
    url: str,
    max_depth: Annotated[int, typer.Option("--max-depth", "-d", min=1)] = 1,
    dedupe: Annotated[
        bool, typer.Option("--dedupe", help="Return each URL once at its lowest depth.")
    ] = False,
    include_crawl: Annotated[
        list[str] | None,
        typer.Option(
            "--include-crawl",
            help="Only crawl child pages matching this glob. Repeatable.",
        ),
    ] = None,
    exclude_crawl: Annotated[
        list[str] | None,
        typer.Option(
            "--exclude-crawl",
            help="Do not crawl child pages matching this glob. Repeatable.",
        ),
    ] = None,
    include_result: Annotated[
        list[str] | None,
        typer.Option(
            "--include-result", help="Only return links matching this glob. Repeatable."
        ),
    ] = None,
    exclude_result: Annotated[
        list[str] | None,
        typer.Option(
            "--exclude-result",
            help="Do not return links matching this glob. Repeatable.",
        ),
    ] = None,
    refresh: Annotated[bool, typer.Option("--refresh", help="Skip cache reads and store fresh acquisitions.")] = False,
    no_store: Annotated[bool, typer.Option("--no-store", help="Skip cache reads and do not persist acquisitions.")] = False,
    max_cache_age: Annotated[int | None, typer.Option("--max-cache-age", min=0)] = None,
    stale_if_error: Annotated[int | None, typer.Option("--stale-if-error", min=0)] = None,
    max_pages: Annotated[int | None, typer.Option("--max-pages", min=1)] = None,
    max_links: Annotated[int | None, typer.Option("--max-links", min=1)] = None,
    max_temp_bytes: Annotated[
        int | None, typer.Option("--max-temp-bytes", min=1)
    ] = None,
) -> None:
    input_value = {
        "url": url,
        "max_depth": max_depth,
        "dedupe": dedupe,
        "include_crawl": include_crawl or [],
        "exclude_crawl": exclude_crawl or [],
        "include_result": include_result or [],
        "exclude_result": exclude_result or [],
        "cache": cache_input(
            refresh=refresh,
            no_store=no_store,
            max_cache_age=max_cache_age,
            stale_if_error=stale_if_error,
        ),
    }
    for name, value in (
        ("max_pages", max_pages),
        ("max_links", max_links),
        ("max_temp_bytes", max_temp_bytes),
    ):
        if value is not None:
            input_value[name] = value
    with ProgressRenderer(console) as progress:
        output = run_action(
            primitive="index",
            input_value=input_value,
            response_adapter=_INDEX_ADAPTER,
            progress_consumer=progress.callback,
        )

    print_task_summary(console, output)
