import typer

from cli.apps import extract, index, paginate, schema, crawl, search

app = typer.Typer(help="Atlas backend CLI.", invoke_without_command=True)
app.command(name="extract")(extract.extract)
app.command(name="index")(index.index)
app.command(name="schema")(schema.schema)
app.command(name="crawl")(crawl.crawl)
app.command(name="paginate")(paginate.paginate)
app.command(name="search")(search.search)


@app.callback()
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()
