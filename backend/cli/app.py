import os

import typer

from cli.apps import extract, index, schema, crawl, search
from cli.config import init_config
from cli.apps.runs import runs

app = typer.Typer(help="Atlas backend CLI.", invoke_without_command=True)
app.command(name="extract")(extract.extract)
app.command(name="index")(index.index)
app.command(name="schema")(schema.schema)
app.command(name="crawl")(crawl.crawl)
app.command(name="search")(search.search)
app.add_typer(runs, name="runs")


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
