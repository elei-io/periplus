from __future__ import annotations

import json
from uuid import UUID

import httpx
import typer

from cli.config import api_url

runs = typer.Typer(help="Inspect and control task runs.")


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


def _print_json(value: object) -> None:
    typer.echo(json.dumps(value, indent=2, default=str))


@runs.command("get")
def get_run(run_id: UUID) -> None:
    with _client() as client:
        response = client.get(f"/task-runs/{run_id}")
        _check(response)
        _print_json(response.json())


@runs.command("result")
def result(run_id: UUID) -> None:
    with _client() as client:
        response = client.get(f"/task-runs/{run_id}/result")
        _check(response)
        _print_json(response.json())


@runs.command("cancel")
def cancel(run_id: UUID) -> None:
    with _client() as client:
        response = client.post(f"/task-runs/{run_id}/cancel")
        _check(response)
        _print_json(response.json())


@runs.command("watch")
def watch(run_id: UUID) -> None:
    with _client() as client:
        with client.stream("GET", f"/task-runs/{run_id}/progress") as response:
            _check(response)
            for line in response.iter_lines():
                if line.startswith("data:"):
                    _print_json(json.loads(line.removeprefix("data:").strip()))
