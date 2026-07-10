from __future__ import annotations

import httpx
import typer

from cli.config import api_url
from worker.process_cleanup import purge_orphaned_playwright_processes

purge = typer.Typer(help="Purge expired operational state.")


def _client() -> httpx.Client:
    return httpx.Client(base_url=api_url(), timeout=None)


def _check(response: httpx.Response) -> None:
    if response.is_success:
        return
    try:
        detail = response.json().get("detail", response.text)
    except ValueError:
        detail = response.text
    raise typer.BadParameter(str(detail or response.reason_phrase))


@purge.command("workers")
def workers() -> None:
    """Remove stale and gracefully stopped worker heartbeat rows."""
    with _client() as client:
        response = client.post("/operations/purge/workers")
        _check(response)
        deleted = int(response.json()["deleted"])

    noun = "worker" if deleted == 1 else "workers"
    typer.echo(f"Purged {deleted} stale {noun}.")


@purge.command("playwright")
def playwright_processes(
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Report orphaned Playwright processes without terminating them.",
    ),
) -> None:
    """Remove Playwright drivers no longer owned by an Atlas worker."""
    result = purge_orphaned_playwright_processes(dry_run=dry_run)
    action = "Found" if dry_run else "Purged"
    targets = (
        f"{result.drivers_found} orphaned Playwright "
        f"{'driver' if result.drivers_found == 1 else 'drivers'}"
    )
    if result.browsers_found:
        targets += (
            f" and {result.browsers_found} detached "
            f"{'browser' if result.browsers_found == 1 else 'browsers'}"
        )
    typer.echo(
        f"{action} {targets} across {result.processes_signalled} "
        f"{'process' if result.processes_signalled == 1 else 'processes'}."
    )
