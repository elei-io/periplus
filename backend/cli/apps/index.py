from typing import Annotated

import typer
from pydantic import TypeAdapter
from rich.console import Console
from rich.table import Table

from actions.index.schemas import IndexLink
from cli.action_runs import run_action
from cli.progress import CrawlProgressRenderer

console = Console()
_INDEX_ADAPTER = TypeAdapter(list[IndexLink])


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
) -> None:
    with CrawlProgressRenderer(console) as progress:
        links = run_action(
            primitive="index",
            input_value={
                "url": url,
                "max_depth": max_depth,
                "dedupe": dedupe,
                "include_crawl": include_crawl or [],
                "exclude_crawl": exclude_crawl or [],
                "include_result": include_result or [],
                "exclude_result": exclude_result or [],
            },
            response_adapter=_INDEX_ADAPTER,
            progress_callback=progress.callback,
        )

    table = Table(title=f"Index links from {url!r}")
    table.add_column("Depth")
    table.add_column("Index")
    table.add_column("Internal")
    table.add_column("Source")
    table.add_column("URL")
    table.add_column("Text")

    for link in links:
        table.add_row(
            str(link.depth),
            str(link.link_index),
            "yes" if link.internal else "no",
            link.source_url,
            link.url,
            link.text or link.title,
        )

    if not links:
        table.caption = "No links found."

    console.print(table)
