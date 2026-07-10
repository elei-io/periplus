from __future__ import annotations

import httpx
import typer

from cli.config import api_url

repository = typer.Typer(help="Inspect and recover repository ingestion.")


def _check(response: httpx.Response) -> None:
    if response.is_success:
        return
    try:
        detail = response.json().get("detail", response.text)
    except ValueError:
        detail = response.text
    raise typer.BadParameter(str(detail or response.reason_phrase))


@repository.command("dead-letters")
def dead_letters(
    limit: int = typer.Option(50, min=1, max=500, help="Maximum failures to show."),
) -> None:
    """List durable terminal ingestion failures through the Atlas API."""

    with httpx.Client(base_url=api_url(), timeout=None) as client:
        response = client.get("/operations/repository/dead-letters", params={"limit": limit})
        _check(response)
        items = response.json()["items"]
    for item in items:
        entry = item["entry"]
        typer.echo(
            f"{item['sequence']}\t{entry['failed_at']}\t{entry['job']['request_id']}\t"
            f"{entry['delivery_count']} deliveries\t{entry['error']}"
        )
    if not items:
        typer.echo("No repository ingestion dead letters.")


@repository.command("requeue")
def requeue(sequence: int = typer.Argument(..., min=1)) -> None:
    """Requeue one dead-letter sequence through the Atlas API."""

    with httpx.Client(base_url=api_url(), timeout=None) as client:
        response = client.post(f"/operations/repository/dead-letters/{sequence}/requeue")
        _check(response)
        entry = response.json()["entry"]
    typer.echo(f"Requeued {entry['job']['request_id']} from dead-letter sequence {sequence}.")
