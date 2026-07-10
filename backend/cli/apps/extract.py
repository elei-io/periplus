from typing import Annotated, Literal

import typer
from pydantic import TypeAdapter
from rich.console import Console
from cli.action_runs import run_action
from cli.cache import cache_input
from cli.progress import ProgressRenderer
from cli.summary import print_task_summary
from tasks.schemas import BoundedTaskOutputJson

console = Console()
_EXTRACT_ADAPTER = TypeAdapter(BoundedTaskOutputJson)


def extract(
    url: str,
    prompt: Annotated[str | None, typer.Option("--prompt", "-p", help="Data extraction instructions.")] = None,
    extract_data: Annotated[
        bool,
        typer.Option("--data/--no-data", help="Enable data extraction with a DataSchema."),
    ] = True,
    extract_query_params: Annotated[
        bool,
        typer.Option("--query-params/--no-query-params", help="Enable query parameter extraction with a QuerySchema."),
    ] = True,
    target_json_example: Annotated[
        str | None,
        typer.Option(
            "--target-json-example",
            help="JSON example for the desired object shape.",
            hidden=True,
        ),
    ] = None,
    schema_type: Annotated[
        Literal["css", "xpath"],
        typer.Option("--schema-type", help="Crawl4AI schema selector type."),
    ] = "css",
    refresh: Annotated[bool, typer.Option("--refresh", help="Skip cache reads and store a fresh acquisition.")] = False,
    no_store: Annotated[bool, typer.Option("--no-store", help="Skip cache reads and do not persist the acquisition.")] = False,
    max_cache_age: Annotated[int | None, typer.Option("--max-cache-age", min=0)] = None,
    stale_if_error: Annotated[int | None, typer.Option("--stale-if-error", min=0)] = None,
) -> None:
    with ProgressRenderer(console) as progress:
        output = run_action(
            primitive="extract",
            input_value={
                "url": url,
                "prompt": prompt,
                "extract_data": extract_data,
                "extract_query_params": extract_query_params,
                "target_json_example": target_json_example,
                "schema_type": schema_type,
                "cache": cache_input(
                    refresh=refresh,
                    no_store=no_store,
                    max_cache_age=max_cache_age,
                    stale_if_error=stale_if_error,
                ),
            },
            response_adapter=_EXTRACT_ADAPTER,
            progress_consumer=progress.callback,
        )

    print_task_summary(console, output)
