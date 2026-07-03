from typing import Annotated, Literal

import typer
from domains.index.service import index_sync as index_service
from rich.console import Console
from rich.table import Table

from cli.progress import CrawlProgressRenderer

console = Console()


def index(
    url: str,
    max_depth: Annotated[int, typer.Option("--max-depth", "-d", min=0)] = 0,
    dedupe: Annotated[
        bool, typer.Option("--dedupe", help="Return each URL once at its lowest depth.")
    ] = False,
    concurrency: Annotated[
        int,
        typer.Option(
            "--concurrency", "-c", min=1, help="Number of pages to crawl in parallel."
        ),
    ] = 10,
    mode: Annotated[
        Literal["static", "dynamic", "app"],
        typer.Option(
            "--mode",
            help="Crawl preset for static pages, dynamic pages, or heavy SPAs.",
        ),
    ] = "static",
    wait: Annotated[
        Literal["none", "stable", "network", "fixed"],
        typer.Option("--wait", help="Wait strategy before collecting links."),
    ] = "none",
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
        links = index_service(
            url=url,
            max_depth=max_depth,
            dedupe=dedupe,
            concurrency=concurrency,
            mode=mode,
            wait=wait,
            include_crawl=include_crawl,
            exclude_crawl=exclude_crawl,
            include_result=include_result,
            exclude_result=exclude_result,
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
