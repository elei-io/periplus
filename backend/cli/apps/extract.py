from typing import Annotated, Literal

import typer
from rich.console import Console
from rich.json import JSON

from cli.progress import CrawlProgressRenderer
from actions.extract.service import extract_sync as extract_service

console = Console()


def extract(
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
        typer.Option("--wait", help="Wait strategy before extraction."),
    ] = "none",
) -> None:
    with CrawlProgressRenderer(console) as progress:
        output = extract_service(
            url=url,
            prompt=prompt,
            target_json_example=target_json_example,
            schema_type=schema_type,
            mode=mode,
            wait=wait,
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

    console.print(JSON.from_data(output.results))
    if output.warnings:
        console.print("[yellow]Quality warnings[/yellow]")
        for warning in output.warnings:
            console.print(f"[yellow]- {warning.code}:[/yellow] {warning.name}")
