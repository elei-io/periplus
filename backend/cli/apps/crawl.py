from typing import Annotated

import typer
from pydantic import TypeAdapter
from rich.console import Console
from rich.table import Table
from cli.action_runs import run_action
from cli.cache import cache_input
from cli.progress import ProgressRenderer
from actions.crawl.schemas import CrawlOutput

console = Console()
_CRAWL_ADAPTER = TypeAdapter(CrawlOutput)


def crawl(
    urls: Annotated[list[str], typer.Argument(help="One or more URLs to crawl.")],
    refresh: Annotated[bool, typer.Option("--refresh", help="Skip cache reads and store a fresh acquisition.")] = False,
    no_store: Annotated[bool, typer.Option("--no-store", help="Skip cache reads and do not persist the acquisition.")] = False,
    max_cache_age: Annotated[int | None, typer.Option("--max-cache-age", min=0, help="Maximum reusable crawl age in seconds.")] = None,
    stale_if_error: Annotated[int | None, typer.Option("--stale-if-error", min=0, help="Maximum stale fallback age in seconds.")] = None,
) -> None:
    with ProgressRenderer(console) as progress:
        output = run_action(
            primitive="crawl",
            input_value={
                "urls": urls,
                "cache": cache_input(
                    refresh=refresh,
                    no_store=no_store,
                    max_cache_age=max_cache_age,
                    stale_if_error=stale_if_error,
                ),
            },
            response_adapter=_CRAWL_ADAPTER,
            progress_consumer=progress.callback,
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
            str(len(page.quality_warnings)),
        )
    if not output.pages:
        table.caption = "No pages crawled."
    console.print(table)
