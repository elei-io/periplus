from typing import Annotated

import typer
from pydantic import TypeAdapter
from rich.console import Console
from rich.table import Table

from actions.search.schemas import SearchResult
from cli.action_runs import run_action
from cli.progress import CrawlProgressRenderer

console = Console()
_SEARCH_ADAPTER = TypeAdapter(list[SearchResult])


def search(
    query: str,
    max_results: Annotated[int, typer.Option("--max-results", "-n", min=1)] = 10,
) -> None:
    with CrawlProgressRenderer(console) as progress:
        results = run_action(
            primitive="search",
            input_value={"query": query, "max_results": max_results},
            response_adapter=_SEARCH_ADAPTER,
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
