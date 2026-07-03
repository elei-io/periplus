from typing import Annotated, Literal

import typer
from rich.console import Console
from rich.json import JSON

from cli.progress import CrawlProgressRenderer
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
    mode: Annotated[
        Literal["static", "dynamic", "app"],
        typer.Option("--mode", help="Crawl preset for static pages, dynamic pages, or heavy SPAs."),
    ] = "static",
    wait: Annotated[
        Literal["none", "stable", "network", "fixed"],
        typer.Option("--wait", help="Wait strategy before generating a schema."),
    ] = "none",
    cache_key: Annotated[
        str | None,
        typer.Option("--cache-key", help="Stable cache key for a reusable schema.", hidden=True),
    ] = None,
    refresh: Annotated[
        bool,
        typer.Option("--refresh", help="Regenerate even if a cached schema exists."),
    ] = False,
) -> None:
    with CrawlProgressRenderer(console) as progress:
        output = schema_service(
            url=url,
            prompt=prompt,
            target_json_example=target_json_example,
            schema_type=schema_type,
            cache_key=cache_key,
            refresh=refresh,
            mode=mode,
            wait=wait,
            progress_callback=progress.callback,
        )

    cached = "cached" if output.cached else "generated"
    console.print(f"[bold]Schema[/bold] {output.schema_id} ({output.schema_type}, {cached})")
    console.print(f"[dim]{output.path}[/dim]")
    console.print(JSON.from_data(output.extraction_schema))
