"""Process-local single-flight storage reads with a short cache; no persisted measurements."""
import asyncio
from datetime import UTC, datetime
import time

from periplus.crawl.runtime.frontier_queue import CAPTURE_STREAM
from periplus.platform.messaging.catalogue_queue import WORK_STREAM, DEAD_LETTER_STREAM
from periplus.operations.storage import readers
from periplus.operations.storage.models import Footprint, StorageReport, Stream


class StorageService:
    def __init__(self, config, sessions, objects, jetstream):
        self.config, self.sessions, self.objects, self.jetstream = config, sessions, objects, jetstream
        self._task: asyncio.Task[StorageReport] | None = None
        self._cached: StorageReport | None = None
        self._cached_at = 0.0

    async def read(self) -> StorageReport:
        if self._task is not None and self._task.done():
            if not self._task.cancelled():
                self._task.exception()  # Observe completion after a caller timed out.
            self._task = None
        if self._cached is not None and time.monotonic() - self._cached_at < 60:
            return self._cached
        if self._task is None:
            self._task = asyncio.create_task(self._collect())
        task = self._task
        try:
            result = await asyncio.wait_for(asyncio.shield(task), timeout=25)
        finally:
            if task.done() and self._task is task:
                self._task = None
        return result

    async def close(self) -> None:
        if self._task is not None:
            await self._task

    async def _collect(self) -> StorageReport:
        started = datetime.now(UTC)
        jobs = [
            asyncio.to_thread(readers.lake, self.config),
            asyncio.to_thread(readers.control_database, self.sessions),
            asyncio.to_thread(readers.lake_metadata, self.config),
            asyncio.to_thread(self._objects),
            self._streams(),
        ]
        lake, control, metadata, objects, streams = await asyncio.gather(*jobs, return_exceptions=True)
        report = StorageReport(as_of=started, collected_at=datetime.now(UTC), sources=[])
        if isinstance(lake, BaseException):
            report.sources.append(Footprint(id="lake", name="Current lake files", basis="DuckLake file metadata", reason="Lake measurements are unavailable or exceeded the read deadline."))
            report.issues.append("Evidence, table sizes and retention state could not be read from DuckLake.")
        else:
            report.evidence, report.tables, report.tables_complete, report.retention = lake
            report.sources.append(Footprint(id="lake", name="Current lake files", bytes=sum(t.bytes for t in report.tables),
                                           complete=report.tables_complete, basis="Registered data and delete files in the current snapshot. Inlined rows occupy metadata storage.",
                                           reason=None if report.tables_complete else "Only the first 200 tables were measured."))
        if isinstance(control, BaseException):
            report.sources.append(Footprint(id="control", name="Control Postgres", basis="Database size", reason="Control database size is unavailable."))
        else:
            source, report.control_tables = control
            report.sources.append(source)
        report.sources.append(metadata if not isinstance(metadata, BaseException) else Footprint(id="metadata", name="DuckLake metadata", basis="Database size", reason="Metadata database size is unavailable."))
        if isinstance(objects, BaseException):
            report.sources.extend([Footprint(id=id, name=name, basis="Object inventory", reason="Object inventory could not be completed.") for id, name in [("raw", "Raw documents"), ("transient", "Transient objects")]])
        else:
            report.sources.extend(objects)
        if isinstance(streams, BaseException):
            report.sources.append(Footprint(id="nats", name="JetStream delivery", basis="Stream state", reason="Stream state is unavailable."))
        else:
            report.streams = streams
            available = [s.bytes for s in streams if s.bytes is not None]
            report.sources.append(Footprint(id="nats", name="JetStream delivery", bytes=sum(available) if available else None,
                                           complete=len(available) == len(streams), basis="Reported bytes for the three work/dead-letter streams; excludes KV, replicas and server overhead.",
                                           reason=None if len(available) == len(streams) else "Some streams could not be measured."))
        self._cached, self._cached_at = report, time.monotonic()
        return report

    def _objects(self) -> list[Footprint]:
        return [readers.inventory(self.objects, prefixes=("html", "documents"), id="raw", name="Raw documents"),
                readers.inventory(self.objects, prefixes=("runtime", "material/staging"), id="transient", name="Transient objects")]

    async def _streams(self) -> list[Stream]:
        result = []
        for name in (CAPTURE_STREAM, WORK_STREAM, DEAD_LETTER_STREAM):
            try:
                info = await asyncio.wait_for(self.jetstream.stream_info(name), timeout=2)
                result.append(Stream(name=name, bytes=info.state.bytes, messages=info.state.messages))
            except Exception:
                result.append(Stream(name=name, bytes=None, messages=None))
        return result
