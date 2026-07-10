"""Rendering for deliberately bounded task-run results."""

from rich.console import Console
from rich.table import Table

from tasks.schemas import BoundedTaskOutputJson


def print_task_summary(console: Console, result: BoundedTaskOutputJson) -> None:
    table = Table(title=f"{result.primitive.capitalize()} complete")
    table.add_column("Measure")
    table.add_column("Count", justify="right")
    for name, value in result.counts.items():
        table.add_row(name.replace("_", " ").capitalize(), f"{value:,}")
    if not result.counts:
        table.caption = "No counts were reported."
    console.print(table)
    if result.catalogue is not None:
        console.print(f"Catalogue run: {result.catalogue.run_id}")
    else:
        console.print("No catalogue data was used by this run.")
