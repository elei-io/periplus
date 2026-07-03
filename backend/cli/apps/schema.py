from typing import Annotated, Literal

import typer
from rich.console import Console
from rich.json import JSON

from domains.schema.service import schema_sync as schema_service

console = Console()


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
    cache_key: Annotated[
        str | None,
        typer.Option("--cache-key", help="Stable cache key for a reusable schema.", hidden=True),
    ] = None,
    refresh: Annotated[
        bool,
        typer.Option("--refresh", help="Regenerate even if a cached schema exists."),
    ] = False,
) -> None:
    output = schema_service(
        url=url,
        prompt=prompt,
        target_json_example=target_json_example,
        schema_type=schema_type,
        cache_key=cache_key,
        refresh=refresh,
    )

    cached = "cached" if output.cached else "generated"
    console.print(f"[bold]Schema[/bold] {output.schema_id} ({output.schema_type}, {cached})")
    console.print(f"[dim]{output.path}[/dim]")
    console.print(JSON.from_data(output.extraction_schema))
