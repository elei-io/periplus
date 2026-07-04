from typing import Annotated, Literal

import typer
from rich.console import Console
from rich.table import Table

from cli.progress import CrawlProgressRenderer
from actions.scrape.service import scrape_sync as scrape_service

console = Console()


def scrape(
    urls: Annotated[list[str], typer.Argument(help="One or more URLs to scrape.")],
    mode: Annotated[
        Literal["static", "dynamic", "app"],
        typer.Option("--mode", help="Crawl preset for static pages, dynamic pages, or heavy SPAs."),
    ] = "static",
    wait: Annotated[
        Literal["none", "stable", "network", "fixed"],
        typer.Option("--wait", help="Wait strategy before returning scrape data."),
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

    table = Table(title="Scrape results")
    table.add_column("URL")
    table.add_column("Success")
    table.add_column("Status")
    table.add_column("HTML Bytes")
    table.add_column("Links")
    table.add_column("Warnings")

    for page in output.pages:
        links = (page.crawl or {}).get("links", {})
        link_count = len(links.get("internal", [])) + len(links.get("external", []))
        table.add_row(
            page.url,
            "yes" if page.success else "no",
            str(page.status_code or ""),
            str(len(page.html or "")),
            str(link_count),
            str(len(page.warnings)),
        )

    if not output.pages:
        table.caption = "No pages scraped."

    console.print(table)
    console.print(
        f"Scraped {output.stats.succeeded}/{output.stats.requested_urls} pages "
        f"in {output.stats.duration_seconds:.2f}s"
    )
