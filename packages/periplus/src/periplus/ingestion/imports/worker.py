"""One bounded archive import step at a time in existing ingestor replicas."""

from uuid import UUID
import asyncio
from datetime import UTC, datetime
import logging

from periplus.ingestion.archive import Archive, capture_key, CaptureRetired
from periplus.ingestion.archive_import import archive_capture
from periplus.ingestion.common_crawl import (
    CommonCrawlClient,
    UnsupportedCapture,
    decode_record,
)
from periplus.ingestion.imports.control import ImportConflict, ImportControl
from periplus.ingestion.imports.schemas import ImportResult, ImportView
from periplus.ingestion.objects.config import object_store_from_env
from periplus.ingestion.objects.store import ObjectStore
from periplus.ingestion.queue import ArchivePublisher
from periplus.platform.health import HealthMonitor
from periplus.platform.messaging.leases import (
    OperationLeaseUnavailable,
    operation_leases,
)
from periplus.platform.postgres.session import SessionLocal
from periplus.platform.execution import bounded_call

log = logging.getLogger(__name__)


class ImportWorker:
    def __init__(
        self,
        control: ImportControl,
        client: CommonCrawlClient,
        store: ObjectStore,
        queue: ArchivePublisher,
    ):
        self.control, self.client, self.store, self.queue = (
            control,
            client,
            store,
            queue,
        )

    async def step(self, job: ImportView) -> None:
        """Checkpoint selection, archive publication, and delivery separately.

        Network/storage work never holds a Postgres transaction. Revision checks
        fence progress against cancellation and overlapping recovery attempts.
        """
        if job.status not in ("queued", "running"):
            return
        try:
            progress, spec = job.progress, job.specification
            if progress.cursor >= len(spec.urls):
                await self.save(job, status="completed")
                return
            url = spec.urls[progress.cursor]
            if progress.current is None:
                item = await asyncio.to_thread(
                    self.client.lookup,
                    url,
                    spec.dataset,
                    since=spec.captured_from,
                    until=spec.captured_until,
                )
                if item is None:
                    await self.finish(job, ImportResult(url=url, status="missing"))
                else:
                    await self.save(job, current=item)
                return
            if progress.archive_key is None:
                item = progress.current
                if (
                    progress.reserved_download_bytes + item.length
                    > spec.max_download_bytes
                ):
                    await self.save(
                        job,
                        status="blocked",
                        error="Archive download budget exhausted; submit remaining URLs as a new bounded job.",
                    )
                    return
                job = await self.save(
                    job,
                    reserved_download_bytes=progress.reserved_download_bytes
                    + item.length,
                )
                data = await asyncio.to_thread(self.client.fetch, item)
                capture = await asyncio.to_thread(decode_record, item, data)
                identity = capture.source.capture_id
                key = capture_key(identity)
                existed = await asyncio.to_thread(self.store.exists, key)
                # Recheck cancellation before starting a durable archive write.
                fresh = await asyncio.to_thread(self.control.get, job.id)
                if fresh.revision != job.revision:
                    return
                await bounded_call(archive_capture, self.store, capture)
                await self.save(job, archive_key=key, already_archived=existed)
                return
            evidence = await asyncio.to_thread(
                Archive(self.store).read,
                UUID(progress.archive_key.rsplit("/", 1)[1].removesuffix(".json.zst")),
            )
            await self.queue.publish(evidence)
            await self.finish(
                job,
                ImportResult(
                    url=url,
                    status="published",
                    capture_id=evidence.capture_id,
                    captured_at=evidence.captured_at,
                    content_sha256=evidence.payload.content_id,
                    stored_bytes=evidence.payload.stored_bytes,
                    already_archived=progress.already_archived,
                ),
            )
        except ImportConflict:
            return
        except (UnsupportedCapture, CaptureRetired) as error:
            await self.finish(
                job,
                ImportResult(
                    url=job.specification.urls[job.progress.cursor],
                    status="retired"
                    if isinstance(error, CaptureRetired)
                    else "unsupported",
                    detail=str(error)[:1000],
                ),
            )
        except Exception as error:
            log.exception(
                "archive import %s blocked at URL %s", job.id, job.progress.cursor
            )
            try:
                await self.save(
                    job,
                    status="blocked",
                    error=f"{type(error).__name__}: {error}"[:1000],
                )
            except ImportConflict:
                pass

    async def save(
        self,
        job: ImportView,
        *,
        status: str = "running",
        error: str | None = None,
        **changes,
    ) -> ImportView:
        progress = job.progress.model_copy(update=changes)
        return await asyncio.to_thread(
            self.control.save, job, progress=progress, status=status, error=error
        )

    async def finish(self, job: ImportView, result: ImportResult) -> None:
        cursor = job.progress.cursor + 1
        await self.save(
            job,
            cursor=cursor,
            current=None,
            archive_key=None,
            already_archived=False,
            results=(*job.progress.results, result),
            status="completed" if cursor == len(job.specification.urls) else "running",
        )


async def run(*, stop: asyncio.Event, monitor: HealthMonitor, leases, client) -> None:
    control = ImportControl(SessionLocal)
    remote = CommonCrawlClient()
    worker = ImportWorker(
        control, remote, object_store_from_env(), ArchivePublisher(client=client)
    )
    try:
        while not stop.is_set():
            try:
                # One provider import lane protects the public CC index from
                # multiplied concurrency as ingestor replicas are added.
                async with operation_leases(
                    leases, ["common-crawl"], phase="archive-import", acquire_timeout=0
                ) as guard:
                    while not stop.is_set() and not guard.lost:
                        jobs = await asyncio.to_thread(control.list, runnable=True)
                        if not jobs:
                            break
                        await worker.step(jobs[0])
                        monitor.subsystem_ready("archive-imports")
                monitor.subsystem_ready("archive-imports")
            except OperationLeaseUnavailable:
                monitor.subsystem_ready("archive-imports")
            except Exception:
                log.exception("archive import control unavailable")
                monitor.subsystem_unavailable(
                    "archive-imports", "archive import control unavailable"
                )
            try:
                await asyncio.wait_for(stop.wait(), timeout=2)
            except TimeoutError:
                pass
    finally:
        remote.close()
