import typer

from domains.domain_1.service import get_domain_1

app = typer.Typer(help="Commands for domain_1.", invoke_without_command=True)


@app.callback()
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


@app.command()
def show() -> None:
    response = get_domain_1()
    typer.echo(response.message)
