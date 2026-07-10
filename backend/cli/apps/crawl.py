from typing import Annotated

import typer
from pydantic import TypeAdapter
from rich.console import Console
from rich.table import Table

from actions.crawl.schemas import CrawlOutput
from cli.action_runs import run_action
from cli.progress import CrawlProgressRenderer

console = Console()
_CRAWL_ADAPTER = TypeAdapter(CrawlOutput)


def crawl(
    urls: Annotated[list[str], typer.Argument(help="One or more URLs to crawl.")],
) -> None:
    with CrawlProgressRenderer(console) as progress:
        output = run_action(
            primitive="crawl",
            input_value={
                "urls": urls,
            },
            response_adapter=_CRAWL_ADAPTER,
            progress_callback=progress.callback,
        )

    table = Table(title="Crawl results")
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
            str(len(page.artifact_warnings)),
        )

    if not output.pages:
        table.caption = "No pages crawled."

    console.print(table)
    console.print(
        f"Crawled {output.stats.succeeded}/{output.stats.requested_urls} pages "
        f"in {output.stats.duration_seconds:.2f}s"
    )
