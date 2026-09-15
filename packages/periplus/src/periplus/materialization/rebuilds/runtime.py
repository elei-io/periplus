"""Archive reconciliation plans work; every replica executes bounded batches."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
import logging
import os
from uuid import UUID

from nats.errors import TimeoutError as NatsTimeoutError
from nats.js.api import ConsumerConfig, AckPolicy

from periplus.ingestion.archive import Archive
from periplus.materialization.rebuilds.models import BatchRecord, BuildRecord
from periplus.ingestion.objects.config import object_store_from_env
from periplus.materialization.rebuilds.control import BuildControl, RUNNING, utc
from periplus.materialization.recipe import (
    preserve_software,
    recipe_digest,
    verify_software,
)
from periplus.materialization.storage import MaterialStore, install_material_schema
from periplus.platform.clickhouse import (
    ClickHouseClient,
    ClickHouseConfig,
    ClickHouseError,
)
from periplus.platform.clickhouse.public import (
    install_public_schema,
    grant_query_target,
)
from periplus.platform.execution import bounded_call, DRAIN_SECONDS
from periplus.platform.messaging.catalogue_queue import (
    MATERIAL_STREAM,
    MATERIAL_SUBJECT,
    WORK_STREAM,
    EVENT_SUBJECT,
)
from periplus.platform.messaging.leases import (
    operation_leases,
    OperationLeaseUnavailable,
)
from nats.js.errors import NotFoundError, BadRequestError
from periplus.retention.identities import WriteClaimUnavailable
from periplus.platform.telemetry import event

log = logging.getLogger(__name__)


def material_consumer(recipe: str) -> str:
    return "materializers_" + recipe


def material_subject(recipe: str) -> str:
    return MATERIAL_SUBJECT + "." + recipe


def retryable(error: Exception) -> bool:
    while error.__cause__ is not None:
        error = error.__cause__
    return isinstance(
        error, (WriteClaimUnavailable, TimeoutError, ConnectionError)
    ) or (
        isinstance(error, ClickHouseError)
        and error.code in ("transport", "159", "241", "202", "252", "394")
    )


def apply_batch(archive: Archive, store: MaterialStore, batch: BatchRecord) -> int:
    captures = []
    for event in archive.range_events(batch.shard, batch.start, batch.end):
        capture = archive.read_event(event)
        if archive.retired(capture.capture_id):
            store.retire(capture, archive)
        else:
            if event.kind == "retirement":
                raise ValueError("Retirement journal entry has no durable tombstone")
            captures.append(capture)
    store.materialize_many(captures, archive)
    return len(captures)


class RebuildRuntime:
    def __init__(self, jetstream, control: BuildControl):
        self.jetstream, self.control = jetstream, control
        self.archive = Archive(object_store_from_env())
        self.client = ClickHouseClient(ClickHouseConfig.from_env())
        self.recipe = recipe_digest()

    def prepare(self, build: BuildRecord) -> None:
        if build.recipe != self.recipe:
            raise ValueError("Build recipe differs from the running worker")
        if build.manifest_key:
            key = build.manifest_key
            manifest = self.archive.read_manifest(key)
            verify_software(self.archive.store, manifest.software_key)
            if manifest.recipe != self.recipe:
                raise ValueError(
                    "Restore requires the software recipe named in the manifest"
                )
        else:
            software_key = preserve_software(self.archive.store)
            key, manifest = self.archive.manifest(self.recipe, software_key)
        install_material_schema(self.client, build.material_database)
        install_public_schema(
            self.client, build.material_database, build.query_database
        )
        grant_query_target(self.client, build.query_database)
        self.control.initialize(build, key, manifest.heads)

    def reclaim(self, build: BuildRecord) -> None:
        # All writes fail-stop in 300s, queries in 45s. The 610s drain includes
        # admission/lease slack; no new batch can claim a draining target.
        if build.query_database == "public_v1":
            for relation in ("capture", "html_element", "link", "page"):
                self.client.execute(f"DROP VIEW IF EXISTS public_v1.{relation}")
        else:
            self.client.execute(f"DROP DATABASE IF EXISTS {build.query_database} SYNC")
        self.client.execute(f"DROP DATABASE IF EXISTS {build.material_database} SYNC")
        self.control.retire(build)

    async def tick(self, guard) -> None:
        heads = await bounded_call(self.archive.heads)
        for build in await asyncio.to_thread(self.control.builds):
            if guard.lost:
                return
            try:
                if build.phase in ("cancelling", "draining"):
                    if utc(build.drain_after) <= datetime.now(UTC):
                        await bounded_call(self.reclaim, build)
                    continue
                if build.phase not in RUNNING:
                    continue
                if build.recipe != self.recipe:
                    continue  # The matching software release owns this build's planner.
                if build.phase == "preparing":
                    await bounded_call(self.prepare, build)
                    continue
                for batch in await asyncio.to_thread(self.control.plan, build, heads):
                    # PG owns bounded pending work, NATS owns delivery. Publication
                    # retries use a fresh delivery ID: a prior message may have
                    # been ACKed while a batch was still claimed by a crashed peer.
                    await self.jetstream.publish(
                        material_subject(build.recipe), str(batch.id).encode()
                    )
                await asyncio.to_thread(self.control.verify, build, heads)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                log.exception(
                    "material_build_blocked",
                    extra={
                        "telemetry": {
                            "operation_id": str(build.id),
                            "operation": "materialization",
                        }
                    },
                )
                await asyncio.to_thread(
                    self.control.change,
                    build.id,
                    build.revision,
                    blocker=f"{type(error).__name__}: {error}"[:1000],
                )


async def coordinator(jetstream, leases, monitor, stop):
    while not stop.is_set():
        try:
            async with operation_leases(
                leases,
                ["corpus-rebuild-planner:" + recipe_digest()],
                phase="materialization",
                acquire_timeout=0,
            ) as guard:
                runtime = RebuildRuntime(jetstream, BuildControl())
                notifications = await jetstream.pull_subscribe_bind(
                    durable="archive-planner", stream=WORK_STREAM
                )
                try:
                    while not stop.is_set() and not guard.lost:
                        await runtime.tick(guard)
                        monitor.subsystem_ready("materialization")
                        try:
                            for message in await notifications.fetch(
                                batch=100, timeout=2
                            ):
                                await message.ack_sync()
                        except (NatsTimeoutError, TimeoutError):
                            pass
                finally:
                    await notifications.unsubscribe()
                    runtime.client.close()
        except OperationLeaseUnavailable:
            monitor.subsystem_ready("materialization")
            await asyncio.sleep(2)
        except Exception:
            log.exception("Archive planner unavailable")
            monitor.subsystem_unavailable(
                "materialization", "Archive planner unavailable"
            )
            await asyncio.sleep(2)


async def consume(
    jetstream, stop, lane: int, *, idle: Callable[[], Awaitable[None]]
):
    control = BuildControl()
    archive = Archive(object_store_from_env())
    client = ClickHouseClient(ClickHouseConfig.from_env())
    recipe = recipe_digest()
    subscription = await jetstream.pull_subscribe_bind(
        durable=material_consumer(recipe), stream=MATERIAL_STREAM
    )
    worker = f"{os.uname().nodename}:{os.getpid()}:{lane}"
    try:
        while not stop.is_set():
            try:
                messages = await subscription.fetch(batch=1, timeout=1)
            except (NatsTimeoutError, TimeoutError):
                if not stop.is_set():
                    await idle()
                continue
            for message in messages:
                claimed = await asyncio.to_thread(
                    control.claim, UUID(message.data.decode()), worker, recipe=recipe
                )
                if claimed is None:
                    await message.ack_sync()
                    continue
                batch, build = claimed
                try:
                    if build.recipe != recipe:
                        raise ValueError("Worker recipe differs from the build")
                    count = await bounded_call(
                        apply_batch,
                        archive,
                        MaterialStore(client, build.material_database),
                        batch,
                    )
                    await asyncio.to_thread(control.finish, batch, count)
                    await message.ack_sync()
                    event(
                        "material_batch_applied",
                        operation_id=str(batch.id),
                        actor=worker,
                        operation="materialization",
                        rows=count,
                        attempt=batch.attempts,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    log.exception(
                        "material_batch_failed",
                        extra={
                            "telemetry": {
                                "operation_id": str(batch.id),
                                "actor": worker,
                                "operation": "materialization",
                            }
                        },
                    )
                    await asyncio.to_thread(
                        control.fail,
                        batch,
                        f"{type(error).__name__}: {error}",
                        retryable(error),
                    )
                    # Failed batches stay in PG for inspection/retry; the work
                    # queue must continue serving other shards and live arrivals.
                    await message.ack_sync()
    finally:
        await subscription.unsubscribe()
        client.close()


async def ensure_material_consumer(jetstream):
    await _ensure_consumer(
        jetstream,
        WORK_STREAM,
        ConsumerConfig(
            durable_name="archive-planner",
            filter_subject=EVENT_SUBJECT,
            ack_policy=AckPolicy.EXPLICIT,
            ack_wait=30,
            max_ack_pending=128,
            max_deliver=-1,
        ),
    )
    recipe = recipe_digest()
    expected = ConsumerConfig(
        durable_name=material_consumer(recipe),
        filter_subject=material_subject(recipe),
        ack_policy=AckPolicy.EXPLICIT,
        ack_wait=DRAIN_SECONDS,
        max_ack_pending=64,
        max_deliver=-1,
    )
    await _ensure_consumer(jetstream, MATERIAL_STREAM, expected)


async def _ensure_consumer(jetstream, stream, expected):
    try:
        info = await jetstream.consumer_info(stream, expected.durable_name)
    except NotFoundError:
        try:
            await jetstream.add_consumer(stream, config=expected)
        except BadRequestError:
            pass
        info = await jetstream.consumer_info(stream, expected.durable_name)
    for name in (
        "filter_subject",
        "ack_policy",
        "ack_wait",
        "max_ack_pending",
        "max_deliver",
    ):
        if getattr(info.config, name) != getattr(expected, name):
            raise RuntimeError(f"Material consumer contract differs: {name}")
