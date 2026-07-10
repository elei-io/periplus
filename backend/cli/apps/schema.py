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
_SCHEMA_ADAPTER = TypeAdapter(BoundedTaskOutputJson)


def schema(
    url: str,
    prompt: Annotated[str, typer.Option("--prompt", "-p", help="Extraction instructions.")],
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
    schema_id: Annotated[
        str | None,
        typer.Option("--schema-id", help="Stable identifier for the generated schema.", hidden=True),
    ] = None,
    refresh: Annotated[bool, typer.Option("--refresh", help="Skip cache reads and store a fresh acquisition.")] = False,
    no_store: Annotated[bool, typer.Option("--no-store", help="Skip cache reads and do not persist the acquisition.")] = False,
    max_cache_age: Annotated[int | None, typer.Option("--max-cache-age", min=0)] = None,
    stale_if_error: Annotated[int | None, typer.Option("--stale-if-error", min=0)] = None,
) -> None:
    with ProgressRenderer(console) as progress:
        output = run_action(
            primitive="schema",
            input_value={
                "url": url,
                "prompt": prompt,
                "target_json_example": target_json_example,
                "schema_type": schema_type,
                "schema_id": schema_id,
                "cache": cache_input(
                    refresh=refresh,
                    no_store=no_store,
                    max_cache_age=max_cache_age,
                    stale_if_error=stale_if_error,
                ),
            },
            response_adapter=_SCHEMA_ADAPTER,
            progress_consumer=progress.callback,
        )

    print_task_summary(console, output)
