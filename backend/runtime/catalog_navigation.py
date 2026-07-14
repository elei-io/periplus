"""Catalog-worker readiness and SQL-edge execution."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from threading import Lock
from uuid import UUID, uuid4
import pyarrow as pa
from sqlglot import exp, parse_one

from nats.errors import TimeoutError as NatsTimeoutError

from config import get_float, get_int, get_str
from db.session import session_scope
from repository.catalogue import catalogue_from_env
from repository.ingestion.health import HealthMonitor
from repository.objects.config import object_store_from_env
from repository.catalogue.query import execute_arrow_query
from repository.exceptions import RepositoryObjectNotFound
from repository.objects.html import RawHtmlRepository, html_object_key
from runtime.navigation import (
    build_navigation_package,
    delete_run_navigation,
    load_navigation_package,
    put_navigation_package,
)
from runtime.navigation_contract import NavigationPackage
from runtime.catalogue_lane import catalogue_operation_lane
from runtime.graph_queue import (
    EDGE_CONSUMER,
    EDGE_SUBJECT,
    GRAPH_STREAM,
    READINESS_CONSUMER,
    READINESS_SUBJECT,
    EdgeWork,
    ReadinessWork,
    connect_nats,
    ensure_graph_progress_storage,
    ensure_graph_storage,
    edge_evaluation_identity,
    list_graph_runs,
    update_edge_evaluation,
)
from runtime.graph_runs import EdgeEvaluationBusy, EdgeEvaluationFailed, evaluate_edge, handle_readiness, resolve_policy_snapshot
from runtime.resource_governor import (
    DURABLE_RESOURCE_WAIT,
    ResourceCapacityUnavailable,
    ResourcePermitLost,
    catalogue_request,
    ensure_resource_governor_storage,
    object_request,
    resource_permits,
)


class EdgeUrlExecutor:
    """Execute one bounded DuckDB edge query and make it interruptible."""

    def __init__(self, navigation_store, package: NavigationPackage) -> None:
        self._lock = Lock()
        self._connection = None
        self._navigation_store = navigation_store
        self._package = package

    def interrupt(self) -> None:
        with self._lock:
            connection = self._connection
        if connection is not None:
            connection.interrupt()

    def __call__(self, sql: str, parameters: dict[str, object]) -> list[str]:
        bound = dict(parameters)
        page_url = str(bound.pop("_page_url"))
        document_id = str(bound.pop("_document_id"))
        navigation_payload = self._load_or_regenerate(
            document_id=document_id,
            page_url=page_url,
        )
        with catalogue_from_env() as catalogue:
            with self._lock:
                self._connection = catalogue.connection
            try:
                catalogue.connection.execute(
                    "SET memory_limit = ?", [get_str("ATLAS_EDGE_QUERY_MEMORY_LIMIT")]
                )
                if "crawl_id" in bound:
                    bound["crawl_id"] = str(bound["crawl_id"])
                crawl_id = bound.get("crawl_id")
                if crawl_id is None:
                    raise ValueError("Edge SQL requires its crawl_id parameter.")
                crawl_id = str(UUID(str(crawl_id)))
                table = pa.ipc.open_file(pa.BufferReader(navigation_payload)).read_all()
                table = table.append_column(
                    "crawl_id", pa.array([crawl_id] * table.num_rows, type=pa.string())
                )
                catalogue.connection.register("atlas_navigation_links", table)
                statement = parse_one(sql, dialect="duckdb")
                for source in statement.find_all(exp.Table):
                    if source.db.lower() == "page" and source.name.lower() == "links":
                        source.set("db", None)
                        source.set("this", exp.to_identifier("atlas_navigation_links"))
                reader = execute_arrow_query(
                    catalogue, statement.sql(dialect="duckdb"), bound
                )
                index = reader.schema.get_field_index("url")
                if index < 0:
                    raise ValueError("Edge SQL did not return its required url column.")
                rows = 0
                output_bytes = 0
                urls: list[str] = []
                for batch in reader:
                    rows += batch.num_rows
                    output_bytes += batch.nbytes
                    if rows > get_int("ATLAS_EDGE_MAX_OUTPUT_ROWS"):
                        raise ValueError("Edge SQL exceeded its row limit.")
                    if output_bytes > get_int("ATLAS_EDGE_MAX_OUTPUT_BYTES"):
                        raise ValueError("Edge SQL exceeded its output-byte limit.")
                    urls.extend(
                        str(value)
                        for value in batch.column(index).to_pylist()
                        if value is not None
                    )
                return urls
            finally:
                with self._lock:
                    self._connection = None

    def _load_or_regenerate(self, *, document_id: str, page_url: str) -> bytes:
        try:
            return load_navigation_package(self._navigation_store, self._package)
        except RepositoryObjectNotFound:
            document_hash = document_id.removeprefix("sha256:")
            html = RawHtmlRepository(self._navigation_store).read(
                html_object_key(document_hash)
            )
            payload, row_count = build_navigation_package(
                html,
                document_id=document_id,
                page_url=page_url,
            )
            rebuilt = put_navigation_package(
                self._navigation_store,
                name=self._package.object_name,
                payload=payload,
                row_count=row_count,
            )
            if rebuilt != self._package:
                self._navigation_store.delete(self._package.object_name)
                raise RuntimeError(
                    "regenerated navigation package does not match its NATS reference"
                )
            return payload


async def _process_readiness(message, runs, requests, progress, jetstream) -> None:
    try:
        wakeup = ReadinessWork.model_validate_json(message.data)
    except Exception:
        logging.exception("discarding invalid navigation-readiness work")
        await message.term()
        return
    try:
        await handle_readiness(
            runs=runs,
            requests=requests,
            progress=progress,
            jetstream=jetstream,
            event=wakeup,
        )
    except Exception:
        await message.nak(delay=1)
        return
    await message.ack()


async def _process_edge(
    message,
    runs,
    requests,
    progress,
    jetstream,
    navigation_store,
    catalogue_operation_lock: asyncio.Lock,
    resource_grants,
) -> None:
    try:
        work = EdgeWork.model_validate_json(message.data)
    except Exception:
        logging.exception("discarding invalid edge-evaluation work")
        await message.term()
        return
    claim_token = uuid4()
    identity = edge_evaluation_identity(
        work.graph_run_id, work.crawl_request_id, work.crawl_id, work.edge_id
    )

    async def keep_alive() -> None:
        interval = max(1.0, get_float("ATLAS_GRAPH_ACK_WAIT_SECONDS") / 3)
        while True:
            await asyncio.sleep(interval)
            await message.in_progress()
            expires_at = datetime.now(UTC) + timedelta(
                seconds=get_float("ATLAS_GRAPH_ACK_WAIT_SECONDS") * 2
            )
            try:
                current = await update_edge_evaluation(
                    requests,
                    identity,
                    lambda value: value if value.claim_token != claim_token else value.model_copy(update={"claim_expires_at": expires_at}),
                )
            except KeyError:
                continue
            if current.claim_token not in {None, claim_token}:
                return

    heartbeat = asyncio.create_task(keep_alive())
    try:
        async with resource_permits(
            resource_grants,
            catalogue_request(
                f"edge:{identity}",
                service_class="critical",
                object_read_units=1,
            ),
            acquire_timeout=DURABLE_RESOURCE_WAIT,
        ):
            async with catalogue_operation_lock:
                with session_scope() as session:
                    await evaluate_edge(
                        runs=runs,
                        requests=requests,
                        progress=progress,
                        jetstream=jetstream,
                        work=work,
                        execute_urls=EdgeUrlExecutor(navigation_store, work.navigation),
                        policy_resolver=lambda url: resolve_policy_snapshot(session, url),
                        claim_token=claim_token,
                    )
    except EdgeEvaluationBusy:
        await message.nak(delay=1)
        return
    except EdgeEvaluationFailed:
        await message.ack()
        return
    except (ResourceCapacityUnavailable, ResourcePermitLost):
        await message.nak(delay=1)
        return
    except Exception:
        await message.nak(delay=1)
        return
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
    # Evaluation records terminal semantic failures itself; redelivery cannot repair SQL.
    await message.ack()


async def run(monitor: HealthMonitor | None = None) -> None:
    stop = asyncio.Event()
    client = await connect_nats()
    jetstream = client.jetstream()
    runs, requests, _workers = await ensure_graph_storage(jetstream)
    progress = await ensure_graph_progress_storage(jetstream)
    resource_grants = await ensure_resource_governor_storage(jetstream)
    navigation_store = object_store_from_env()
    readiness = await jetstream.pull_subscribe(
        READINESS_SUBJECT, durable=READINESS_CONSUMER, stream=GRAPH_STREAM
    )
    edges = await jetstream.pull_subscribe(
        EDGE_SUBJECT, durable=EDGE_CONSUMER, stream=GRAPH_STREAM
    )
    if monitor is not None:
        monitor.subsystem_ready("navigation")
    capacity = get_int("ATLAS_INGESTION_WORKER_CONCURRENCY")
    catalogue_operation_lock = catalogue_operation_lane()
    active: set[asyncio.Task] = set()
    cleaned_runs = set()
    next_cleanup = 0.0
    try:
        while not stop.is_set():
            if time.monotonic() >= next_cleanup:
                now = datetime.now(UTC)
                grace = get_float("ATLAS_NAVIGATION_CLEANUP_GRACE_SECONDS")
                for graph_run in await list_graph_runs(runs):
                    if (
                        graph_run.id not in cleaned_runs
                        and graph_run.completed_at is not None
                        and (now - graph_run.completed_at).total_seconds() >= grace
                    ):
                        try:
                            async with resource_permits(
                                resource_grants,
                                object_request(
                                    f"navigation-cleanup:{graph_run.id}",
                                    direction="write",
                                    byte_count=1,
                                    service_class="critical",
                                ),
                                acquire_timeout=0,
                            ):
                                await asyncio.to_thread(
                                    delete_run_navigation,
                                    navigation_store,
                                    graph_run.id,
                                )
                        except ResourceCapacityUnavailable:
                            # Cleanup is off-path. Shared object-store pressure is
                            # ordinary backpressure, so defer it without blocking
                            # navigation work or alarming the operator.
                            continue
                        except Exception:
                            logging.warning(
                                "navigation package cleanup failed for graph run %s",
                                graph_run.id,
                                exc_info=True,
                            )
                        else:
                            cleaned_runs.add(graph_run.id)
                next_cleanup = time.monotonic() + 10
            completed = {task for task in active if task.done()}
            for task in completed:
                if task.cancelled():
                    continue
                error = task.exception()
                if error is not None:
                    logging.error(
                        "navigation task exited unexpectedly",
                        exc_info=(type(error), error, error.__traceback__),
                    )
                    if monitor is not None:
                        monitor.subsystem_unavailable(
                            "navigation", str(error) or type(error).__name__
                        )
            active.difference_update(completed)
            available = capacity - len(active)
            if available <= 0:
                await asyncio.sleep(0.05)
                continue
            fetched = False
            for subscription, processor in ((readiness, _process_readiness), (edges, _process_edge)):
                if available <= 0:
                    break
                try:
                    messages = await subscription.fetch(batch=available, timeout=0.1)
                except (NatsTimeoutError, asyncio.TimeoutError):
                    continue
                fetched = fetched or bool(messages)
                for message in messages:
                    arguments = (message, runs, requests, progress, jetstream)
                    if processor is _process_edge:
                        arguments += (
                            navigation_store,
                            catalogue_operation_lock,
                            resource_grants,
                        )
                    active.add(asyncio.create_task(processor(*arguments)))
                    available -= 1
                    if available <= 0:
                        break
            if not fetched:
                await asyncio.sleep(0.05)
    finally:
        stop.set()
        await asyncio.gather(*active, return_exceptions=True)
        await client.drain()
