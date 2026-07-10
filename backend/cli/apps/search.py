from typing import Annotated

import typer
from pydantic import TypeAdapter
from rich.console import Console
from rich.table import Table
from actions.search.schemas import SearchProvider
from cli.action_runs import run_action
from cli.cache import cache_input
from cli.progress import ProgressRenderer
from actions.search.schemas import SearchResult

console = Console()
_SEARCH_ADAPTER = TypeAdapter(list[SearchResult])


def search(
    query: str,
    max_pages: Annotated[int, typer.Option("--max-pages", "-p", min=1, max=25)] = 1,
    provider: Annotated[SearchProvider, typer.Option("--provider")] = "duckduckgo",
    refresh: Annotated[bool, typer.Option("--refresh", help="Skip cache reads and store fresh acquisitions.")] = False,
    no_store: Annotated[bool, typer.Option("--no-store", help="Skip cache reads and do not persist acquisitions.")] = False,
    max_cache_age: Annotated[int | None, typer.Option("--max-cache-age", min=0)] = None,
    stale_if_error: Annotated[int | None, typer.Option("--stale-if-error", min=0)] = None,
) -> None:
    with ProgressRenderer(console) as progress:
        results = run_action(
            primitive="search",
            input_value={
                "query": query,
                "max_pages": max_pages,
                "provider": provider,
                "cache": cache_input(
                    refresh=refresh,
                    no_store=no_store,
                    max_cache_age=max_cache_age,
                    stale_if_error=stale_if_error,
                ),
            },
            response_adapter=_SEARCH_ADAPTER,
            progress_consumer=progress.callback,
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
