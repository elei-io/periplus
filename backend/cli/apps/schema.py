from typing import Annotated, Literal

import typer
from pydantic import TypeAdapter
from rich.console import Console
from rich.json import JSON

from actions.shared.extract_schema.schemas import SchemaOutput
from cli.action_runs import run_action
from cli.progress import CrawlProgressRenderer

console = Console()
_SCHEMA_ADAPTER = TypeAdapter(SchemaOutput)


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
    schema_id: Annotated[
        str | None,
        typer.Option("--schema-id", help="Stable identifier for the generated schema.", hidden=True),
    ] = None,
) -> None:
    with CrawlProgressRenderer(console) as progress:
        output = run_action(
            primitive="schema",
            input_value={
                "url": url,
                "prompt": prompt,
                "target_json_example": target_json_example,
                "schema_type": schema_type,
                "schema_id": schema_id,
                "mode": mode,
                "wait": wait,
            },
            response_adapter=_SCHEMA_ADAPTER,
            progress_callback=progress.callback,
        )

    console.print(f"[bold]Schema[/bold] {output.schema_id} ({output.schema_type}, generated)")
    console.print(JSON.from_data(output.extraction_schema))
