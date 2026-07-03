import typer

from cli.apps import search

app = typer.Typer(help="Atlas backend CLI.", invoke_without_command=True)
app.command(name="search")(search.search)


@app.callback()
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()
