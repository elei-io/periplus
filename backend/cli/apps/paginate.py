from typing import Annotated

import typer
from pydantic import TypeAdapter
from rich.console import Console
from rich.table import Table

from actions.paginate.schemas import PaginateOutput
from actions.shared.crawl import CrawlMode, CrawlWait
from cli.action_runs import run_action
from cli.progress import CrawlProgressRenderer

console = Console()
_PAGINATE_ADAPTER = TypeAdapter(PaginateOutput)


def paginate(
    url: str,
    max_pages: Annotated[int, typer.Option("--max-pages", "-p", min=1, max=25)] = 5,
    mode: Annotated[CrawlMode, typer.Option("--mode")] = "app",
    wait: Annotated[CrawlWait, typer.Option("--wait")] = "stable",
    fresh: Annotated[bool, typer.Option("--fresh", help="Generate a fresh pagination schema.")] = False,
) -> None:
    with CrawlProgressRenderer(console) as progress:
        output = run_action(
            primitive="paginate",
            input_value={
                "url": url,
                "max_pages": max_pages,
                "mode": mode,
                "wait": wait,
                "reuse_existing": not fresh,
            },
            response_adapter=_PAGINATE_ADAPTER,
            progress_callback=progress.callback,
        )

    table = Table(title=f"Pagination for {url}")
    table.add_column("#")
    table.add_column("URL")
    table.add_column("Items")
    table.add_column("New")
    table.add_column("Status")
    for page in output.pages:
        table.add_row(
            str(page.index + 1),
            page.url,
            str(page.item_count),
            str(page.new_item_count),
            "ok" if page.success else page.error or "failed",
        )

    console.print(table)
    if output.plan:
        console.print(f"Strategy: {output.plan.kind} ({'reused' if output.plan.reused else 'generated'})")
        console.print(f"Item selector: {output.plan.item_selector}")
    console.print(f"Stopped: {output.stopped_reason}")
