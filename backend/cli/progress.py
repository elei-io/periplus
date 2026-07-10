from collections import deque
import time
from types import TracebackType
from urllib.parse import urlparse

from rich.console import Console
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Column
from rich.table import Table
from rich.text import Text

from actions.shared.progress import ProgressEvent


class ProgressRenderer:
    def __init__(self, console: Console, tail_count: int = 15) -> None:
        self._console = console
        self._tail_count = max(1, tail_count)
        self._visible_task_keys: deque[str] = deque()
        self._events_by_phase: dict[str, ProgressEvent] = {}
        self._started_at_by_phase: dict[str, float] = {}
        self._statuses_by_phase: dict[str, str] = {}
        self._started_count = 0
        self._succeeded_count = 0
        self._failed_count = 0
        self._started_at = time.perf_counter()
        self._live = Live(
            self._render(),
            console=console,
            refresh_per_second=8,
            transient=False,
            vertical_overflow="visible",
        )

    def __enter__(self) -> "ProgressRenderer":
        self._live.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._live.__exit__(exc_type, exc_value, traceback)
        self._console.print()

    def callback(self, event: ProgressEvent) -> None:
        self._record_event(event)
        task_key = event.operation_id or f"{event.phase}:{event.resource or '-'}"
        try:
            self._visible_task_keys.remove(task_key)
        except ValueError:
            pass
        self._visible_task_keys.append(task_key)

        self._prune_tail()
        self._live.update(self._render())

    def _record_event(self, event: ProgressEvent) -> None:
        task_key = event.operation_id or f"{event.phase}:{event.resource or '-'}"
        self._events_by_phase[task_key] = event
        self._started_at_by_phase.setdefault(task_key, time.perf_counter())

        previous_status = self._statuses_by_phase.get(task_key)
        if previous_status == event.status:
            return

        if previous_status is None:
            self._started_count += 1
        elif previous_status == "succeeded":
            self._succeeded_count -= 1
        elif previous_status == "failed":
            self._failed_count -= 1

        if event.status == "succeeded":
            self._succeeded_count += 1
        elif event.status == "failed":
            self._failed_count += 1

        self._statuses_by_phase[task_key] = event.status

    def _prune_tail(self) -> None:
        while len(self._visible_task_keys) > self._tail_count:
            self._visible_task_keys.popleft()

    @property
    def _active_count(self) -> int:
        return self._started_count - self._succeeded_count - self._failed_count

    @property
    def _completed_count(self) -> int:
        return self._succeeded_count + self._failed_count

    def _render(self) -> Panel:
        return Panel(
            Group(
                self._render_summary(),
                self._render_recent_pages(),
            ),
            title="[bold cyan]Atlas crawl[/]",
            title_align="left",
            border_style="cyan",
            padding=(0, 1),
        )

    def _render_summary(self) -> Table:
        elapsed = time.perf_counter() - self._started_at
        rate = self._completed_count / max(elapsed, 0.1)
        summary = Table.grid(expand=True)
        summary.add_column(ratio=1)
        summary.add_column(justify="right", no_wrap=True)
        summary.add_row(
            Text.assemble(
                ("seen ", "dim"),
                (f"{self._started_count:>4}", "bold"),
                ("  active ", "dim"),
                (f"{self._active_count:>3}", "bold cyan"),
                ("  ok ", "dim"),
                (f"{self._succeeded_count:>4}", "bold green"),
                ("  failed ", "dim"),
                (f"{self._failed_count:>3}", "bold red"),
            ),
            Text.assemble(
                ("elapsed ", "dim"),
                (self._format_seconds(elapsed), "bold"),
                ("  rate ", "dim"),
                (f"{rate:>4.1f}/s", "bold"),
            ),
        )
        return summary

    def _render_recent_pages(self) -> Table:
        table = Table(
            Column("", width=2, no_wrap=True),
            Column("phase", width=22, no_wrap=True, style="cyan"),
            Column("age", width=7, no_wrap=True, justify="right"),
            Column("time", width=8, no_wrap=True, justify="right"),
            Column("page", ratio=1, no_wrap=True, overflow="ellipsis"),
            box=None,
            expand=True,
            header_style="dim",
            padding=(0, 1),
            show_edge=False,
        )
        now = time.perf_counter()
        for task_key in self._visible_task_keys:
            event = self._events_by_phase[task_key]
            started_at = self._started_at_by_phase[task_key]
            table.add_row(
                self._state_icon(event),
                event.phase.replace("_", " "),
                self._format_seconds(now - started_at),
                self._duration_text(event, now - started_at),
                event.message or self._display_url(event.resource or "-"),
            )

        return table

    def _state_icon(self, event: ProgressEvent) -> Text:
        if event.status in {"waiting", "started"}:
            return Text("●", style="cyan")

        if event.status == "succeeded":
            return Text("✓", style="green")

        return Text("×", style="red")

    def _duration_text(self, event: ProgressEvent, elapsed: float) -> Text:
        if event.status in {"waiting", "started"}:
            return Text(event.status, style="dim")

        duration = event.duration if event.duration is not None else elapsed
        style = "green" if event.status == "succeeded" else "red"
        return Text(self._format_seconds(duration), style=style)

    def _display_url(self, url: str) -> str:
        parsed_url = urlparse(url)
        if not parsed_url.netloc:
            return url

        path = parsed_url.path or "/"
        if parsed_url.query:
            path = f"{path}?{parsed_url.query}"
        return f"{parsed_url.netloc}{path}"

    def _format_seconds(self, seconds: float) -> str:
        if seconds < 60:
            return f"{seconds:0.1f}s"

        minutes, remaining_seconds = divmod(int(seconds), 60)
        return f"{minutes:d}m{remaining_seconds:02d}s"
