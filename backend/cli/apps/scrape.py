from typing import Annotated, Literal

import typer
from rich.console import Console
from rich.table import Table

from cli.progress import CrawlProgressRenderer
from domains.scrape.service import scrape_sync as scrape_service

console = Console()


def scrape(
    urls: Annotated[list[str], typer.Argument(help="One or more URLs to scrape.")],
    mode: Annotated[
        Literal["static", "dynamic", "app"],
        typer.Option("--mode", help="Crawl preset for static pages, dynamic pages, or heavy SPAs."),
    ] = "static",
    wait: Annotated[
        Literal["none", "stable", "network", "fixed"],
        typer.Option("--wait", help="Wait strategy before writing artifacts."),
    ] = "none",
    concurrency: Annotated[
        int,
        typer.Option("--concurrency", "-c", min=1, help="Number of pages to scrape in parallel."),
    ] = 10,
) -> None:
    with CrawlProgressRenderer(console) as progress:
        output = scrape_service(
            urls=urls,
            mode=mode,
            wait=wait,
            concurrency=concurrency,
            progress_callback=progress.callback,
        )

    table = Table(title="Scrape artifacts")
    table.add_column("URL")
    table.add_column("Success")
    table.add_column("Cached")
    table.add_column("Artifacts")
    table.add_column("Bytes")
    table.add_column("Warnings")
    table.add_column("Cache Dir")

    for page in output.pages:
        table.add_row(
            page.url,
            "yes" if page.success else "no",
            "yes" if page.cached else "no",
            str(len(page.artifacts)),
            str(sum(artifact.bytes for artifact in page.artifacts)),
            str(len(page.warnings)),
            page.cache_dir,
        )

    if not output.pages:
        table.caption = "No pages scraped."

    console.print(table)
    console.print(
        f"Wrote {output.stats.artifacts} artifacts "
        f"({output.stats.cache_hits} cache hits, {output.stats.bytes_written} bytes) "
        f"to {output.cache_root}"
    )
