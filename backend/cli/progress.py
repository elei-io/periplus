from types import TracebackType

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from shared.progress import CrawlProgressEvent


class CrawlProgressRenderer:
    def __init__(self, console: Console) -> None:
        self._tasks_by_phase: dict[tuple[str, str], int] = {}
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[cyan]{task.fields[label]}[/]"),
            TextColumn("{task.fields[url]}"),
            TextColumn("[bold]{task.fields[status]}[/]"),
            console=console,
        )

    def __enter__(self) -> "CrawlProgressRenderer":
        self._progress.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._progress.__exit__(exc_type, exc_value, traceback)

    def callback(self, event: CrawlProgressEvent) -> None:
        task_key = (event.url, event.label)
        task_id = self._tasks_by_phase.get(task_key)
        if task_id is None:
            task_id = self._progress.add_task(
                "",
                total=None,
                label=event.label,
                url=event.url,
                status=self._status_text(event),
            )
            self._tasks_by_phase[task_key] = task_id
        else:
            self._progress.update(
                task_id,
                status=self._status_text(event),
            )

        if event.status != "started":
            self._progress.update(task_id, total=1, completed=1)
            self._progress.stop_task(task_id)

    def _status_text(self, event: CrawlProgressEvent) -> str:
        if event.status == "started":
            return "loading"

        duration = "" if event.duration is None else f" {event.duration:.2f}s"
        if event.status == "succeeded":
            return f"✓{duration}"

        return f"✗{duration}"
