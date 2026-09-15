"""One operation owner coordinates bounded live work and resumable historical pages.

All replicas are eligible. The owner drains its bounded writer before releasing
its NATS lease. Exact Postgres write claims remain the publication fence.
"""
import asyncio
import logging
from periplus.platform.clickhouse import ClickHouseError
from periplus.retention.identities import WriteClaimUnavailable
from datetime import UTC, datetime
from nats.js.errors import NotFoundError
from nats.errors import TimeoutError as NatsTimeoutError
from periplus.ingestion.queue import IngestionJob, STREAM, DEAD_LETTER_STREAM
from periplus.ingestion.storage import evidence_digest
from periplus.ingestion.service import repository_ingestor_from_env
from periplus.materialization.storage import MaterialStore, MaterialInputError, install_material_schema
from periplus.materialization.rebuilds.control import BuildControl, RUNNING, SEMANTIC_VERSION
from periplus.materialization.rebuilds.delivery import Barrier, acknowledge_barrier, ensure_target_consumer
from periplus.materialization.rebuilds.source import plan_ranges, read_page, decode_visit
from periplus.platform.clickhouse.public import install_public_schema, grant_query_target
from periplus.platform.messaging.catalogue_queue import BARRIER_SUBJECT
from periplus.platform.messaging.leases import operation_leases, OperationLeaseUnavailable

logger = logging.getLogger(__name__)


def failure_reason(exc: Exception) -> str:
    cause = exc
    while cause.__cause__ is not None:
        cause = cause.__cause__
    retryable = isinstance(cause, (WriteClaimUnavailable, TimeoutError, ConnectionError)) or (
        isinstance(cause, ClickHouseError) and cause.code in ('transport', '159', '241', '202', '252'))
    detail = str(exc)[:180] if isinstance(exc, (MaterialInputError, ValueError)) else type(exc).__name__
    return ('retrying: ' if retryable else '') + detail


async def bounded_write(function, *args):
    from periplus.materialization.materializer import _fail_stop
    from threading import Timer
    timer = Timer(300, _fail_stop)
    timer.daemon = True
    timer.start()
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    finally:
        if not task.done():
            await asyncio.shield(task)
        timer.cancel()
        timer.join()


class RebuildRuntime:
    def __init__(self, jetstream, control: BuildControl):
        self.jetstream, self.control = jetstream, control
        self.subscriptions = {}
        self.ingestor = None
        self.history_ingestor = None
        self.backfill_task = None

    async def open(self):
        self.ingestor = await asyncio.to_thread(repository_ingestor_from_env)
        await asyncio.to_thread(self.ingestor.validate)
        self.history_ingestor = await asyncio.to_thread(repository_ingestor_from_env)

    async def close(self):
        if self.backfill_task:
            await asyncio.shield(self.backfill_task)
        if self.history_ingestor:
            await asyncio.to_thread(self.history_ingestor.close)
        for subscription in self.subscriptions.values():
            await subscription.unsubscribe()
        if self.ingestor:
            await asyncio.to_thread(self.ingestor.close)

    async def tick(self, guard) -> None:
        builds = await asyncio.to_thread(self.control.builds)
        # One bounded page per round, after every live target gets an opportunity.
        candidate = None
        for build in builds:
            if guard.lost:
                return
            try:
                if build.phase in ('cancelling', 'draining'):
                    if build.drain_after <= datetime.now(UTC):
                        try:
                            await self.jetstream.delete_consumer(STREAM, build.consumer)
                        except NotFoundError:
                            pass
                        await asyncio.to_thread(self.control.change, build.id, build.revision,
                                                phase='cancelled' if build.phase == 'cancelling' else 'retired',
                                                protected=build.phase == 'draining')
                    continue
                if build.phase not in RUNNING:
                    continue
                if build.blocker:
                    if not build.blocker.startswith('retrying:') or (datetime.now(UTC) - build.updated_at).total_seconds() < 5:
                        continue
                    await asyncio.to_thread(self.control.change, build.id, build.revision, blocker=None)
                if build.semantic_version != SEMANTIC_VERSION:
                    raise ValueError('worker_semantics_mismatch')
                actual, consumer, ingestion = await ensure_target_consumer(self.jetstream, build)
                await asyncio.to_thread(self.control.change, build.id, build.revision,
                    stream_created=actual[0], consumer_created=actual[1], ingestion_created=actual[2],
                    ingestion_floor=ingestion.ack_floor.stream_seq, material_floor=consumer.ack_floor.stream_seq,
                    live_pending=consumer.num_pending + consumer.num_ack_pending)
                if build.phase == 'preparing':
                    await bounded_write(self.prepare, build)
                    continue
                if build.consumer not in self.subscriptions:
                    self.subscriptions[build.consumer] = await self.jetstream.pull_subscribe_bind(
                        durable=build.consumer, stream=STREAM)
                await self.live(build)
                if build.phase == 'building' and not build.paused:
                    candidate = build
                elif build.phase in ('verifying', 'ready'):
                    await self.verify(build)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception('material build blocked build=%s', build.id)
                reason = failure_reason(exc)
                await asyncio.to_thread(self.control.change, build.id, build.revision, blocker=reason)
        if candidate and not guard.lost and (self.backfill_task is None or self.backfill_task.done()):
            self.backfill_task = asyncio.create_task(self.history(candidate))

    async def history(self, candidate):
        try:
            current = await asyncio.to_thread(self.control.get, candidate.id)
            if current.phase == 'building' and not current.paused and not current.blocker:
                await bounded_write(self.backfill, current)
        except Exception as exc:
            logger.exception('historical page blocked build=%s', candidate.id)
            await asyncio.to_thread(self.control.change, candidate.id, candidate.revision, blocker=failure_reason(exc))

    def prepare(self, build):
        client = self.ingestor.evidence_store.client
        install_material_schema(client, build.material_database)
        install_public_schema(client, build.material_database, build.query_database)
        grant_query_target(client, build.query_database)
        self.control.plan(build.id, build.revision, plan_ranges(client))

    async def live(self, build):
        try:
            messages = await self.subscriptions[build.consumer].fetch(batch=1, timeout=0.1)
        except (NatsTimeoutError, TimeoutError):
            return
        for message in messages:
            try:
                if await acknowledge_barrier(message):
                    continue
                job = IngestionJob.model_validate_json(message.data)
                if job.visit:
                    store = MaterialStore(self.ingestor.evidence_store.client, build.material_database)
                    try:
                        await bounded_write(store.materialize, job.visit, self.ingestor)
                    except Exception as exc:
                        raise MaterialInputError(job.identity, exc) from exc
                    receipt = await asyncio.to_thread(self.ingestor.evidence_store.receipt,
                        'visit', job.identity, evidence_digest(job.visit))
                    if receipt is None:
                        await message.nak(delay=1)
                        continue
                await message.ack_sync()
            except BaseException:
                await message.nak(delay=1)
                raise

    def backfill(self, build):
        client = self.history_ingestor.evidence_store.client
        ranges = self.control.ranges(build.id)
        row = next((item for item in ranges if not item.done), None)
        if row is None:
            self.control.change(build.id, build.revision, phase='verifying')
            return
        rows = read_page(client, row.month, row.upper, row.cursor, build.page_size)
        store = MaterialStore(client, build.material_database)
        store.materialize_many([decode_visit(source) for source in rows], self.history_ingestor)
        cursor = [rows[-1][key] for key in ('requested_url', 'finished_at', 'visit_id')] if rows else None
        self.control.checkpoint(build.id, build.revision, row.month, cursor, len(rows), len(rows) < build.page_size)

    async def verify(self, build):
        if build.barrier is None:
            result = await self.jetstream.publish(BARRIER_SUBJECT, Barrier(build_id=build.id).model_dump_json().encode())
            await asyncio.to_thread(self.control.change, build.id, build.revision, barrier=result.seq)
            return
        _, material, ingestion = await ensure_target_consumer(self.jetstream, build)
        # Ingestor TERM/dead-letter is not success. Retained candidate deliveries
        # must each reconcile an authoritative source receipt before readiness.
        if min(material.ack_floor.stream_seq, ingestion.ack_floor.stream_seq) >= build.barrier:
            if (await self.jetstream.stream_info(DEAD_LETTER_STREAM)).state.messages:
                raise ValueError('unresolved_ingestion_failures')
            await asyncio.to_thread(self.control.change, build.id, build.revision, phase='ready')



async def run_rebuilds(jetstream, leases, monitor, stop):
    while not stop.is_set():
        try:
            async with operation_leases(leases, ['html-rebuild-controller'], phase='materialization', acquire_timeout=0) as guard:
                runtime = RebuildRuntime(jetstream, BuildControl())
                try:
                    await asyncio.to_thread(runtime.control.fence_workers)
                    await runtime.open()
                    while not stop.is_set() and not guard.lost:
                        await runtime.tick(guard)
                        monitor.subsystem_ready('materialization')
                        await asyncio.sleep(0.2)
                finally:
                    await runtime.close()
        except OperationLeaseUnavailable:
            await asyncio.sleep(1)
