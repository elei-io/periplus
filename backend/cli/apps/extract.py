from typing import Annotated, Literal

import typer
from pydantic import TypeAdapter
from rich.console import Console
from rich.json import JSON

from actions.extract.schemas import ExtractOutput
from cli.action_runs import run_action
from cli.progress import CrawlProgressRenderer

console = Console()
_EXTRACT_ADAPTER = TypeAdapter(ExtractOutput)


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
) -> None:
    with CrawlProgressRenderer(console) as progress:
        output = run_action(
            primitive="extract",
            input_value={
                "url": url,
                "prompt": prompt,
                "extract_data": extract_data,
                "extract_query_params": extract_query_params,
                "target_json_example": target_json_example,
                "schema_type": schema_type,
            },
            response_adapter=_EXTRACT_ADAPTER,
            progress_callback=progress.callback,
        )

    if output.source:
        console.print(
            f"[bold]Extract[/bold] {output.url} "
            f"(schema: {output.source.schema_id}, {output.source.schema_type})"
        )
    else:
        console.print(f"[bold]Extract[/bold] {output.url}")

    if not output.success:
        console.print(f"[red]{output.error or 'Extraction failed.'}[/red]")
        raise typer.Exit(1)

    if output.results:
        console.print(JSON.from_data(output.results))
    if output.query_params:
        console.print("[bold]Query parameters[/bold]")
        console.print(JSON.from_data(output.query_params.model_dump(mode="json")))
    if output.warnings:
        console.print("[yellow]Quality warnings[/yellow]")
        for warning in output.warnings:
            console.print(f"[yellow]- {warning.code}:[/yellow] {warning.name}")
