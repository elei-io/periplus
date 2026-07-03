import typer

from cli.apps import domain_1

app = typer.Typer(help="Atlas backend CLI.", invoke_without_command=True)
app.add_typer(domain_1.app, name="domain-1")


@app.callback()
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()
