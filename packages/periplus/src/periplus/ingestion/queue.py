"""Archive-first publication using one process-owned NATS connection."""

import asyncio
from periplus.ingestion.archive import Archive, ArchiveEvent
from periplus.ingestion.captures import Capture, from_visit
from periplus.ingestion.objects.config import object_store_from_env
from periplus.platform.messaging.client import connect_nats
from periplus.platform.messaging.catalogue_queue import (
    WORK_STREAM,
    EVENT_SUBJECT,
    ensure_catalogue_work_stream,
)


class ArchivePublisher:
    def __init__(self, client=None, archive: Archive | None = None):
        self.client = client
        self.archive = archive or Archive(object_store_from_env())
        self._owns_client = client is None

    async def connect(self) -> None:
        if self.client is None:
            self.client = await connect_nats()
        await ensure_catalogue_work_stream(self.client.jetstream())

    async def close(self) -> None:
        if self._owns_client and self.client is not None:
            await self.client.drain()
            self.client = None

    async def check_available(self) -> None:
        await asyncio.wait_for(self.client.flush(), timeout=5)
        await asyncio.wait_for(
            self.client.jetstream().stream_info(WORK_STREAM), timeout=5
        )

    async def announce(self, event: ArchiveEvent) -> None:
        await self.client.jetstream().publish(
            EVENT_SUBJECT,
            event.model_dump_json().encode(),
            headers={"Nats-Msg-Id": f"archive:{event.shard}:{event.sequence}"},
        )

    async def publish(self, capture: Capture) -> ArchiveEvent:
        from periplus.platform.execution import bounded_call
        from periplus.retention.identities import write_claims

        def commit():
            with write_claims(
                {
                    "capture": [str(capture.capture_id)],
                    "content": [capture.payload.content_id] if capture.payload else [],
                }
            ):
                return self.archive.commit(capture)

        event = await bounded_call(commit)
        await self.announce(event)
        return event

    async def enqueue_visit(self, evidence) -> ArchiveEvent:
        return await self.publish(from_visit(evidence))
