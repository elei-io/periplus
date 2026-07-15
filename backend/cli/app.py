import os

import typer

from cli.config import init_config
from cli.apps.repository import repository

app = typer.Typer(help="Atlas backend CLI.", invoke_without_command=True)
app.add_typer(repository, name="repository")


@app.command(name="init")
def init(url: str = typer.Option(..., "--url", help="Atlas API base URL.")) -> None:
    init_config(url)


@app.callback()
def main(
    ctx: typer.Context,
    api_url: str | None = typer.Option(None, "--api-url", help="Override the Atlas API base URL."),
) -> None:
    if api_url:
        os.environ["ATLAS_API_URL"] = api_url
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()
