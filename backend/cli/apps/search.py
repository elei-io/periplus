from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from cli.progress import CrawlProgressRenderer
from actions.search.service import search_sync as search_service

console = Console()


def search(
    query: str,
    max_results: Annotated[int, typer.Option("--max-results", "-n", min=1)] = 10,
) -> None:
    with CrawlProgressRenderer(console) as progress:
        results = search_service(
            query=query,
            max_results=max_results,
            progress_callback=progress.callback,
        )

    table = Table(title=f"Search results for {query!r}")
    table.add_column("Title")
    table.add_column("URL")
    table.add_column("Description")

    for result in results:
        table.add_row(result.title, result.url, result.description)

    if not results:
        table.caption = "No results found."

    console.print(table)
