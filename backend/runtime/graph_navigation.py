"""Acquisition-owned readiness and bounded SQL-edge execution."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from threading import Lock
from uuid import UUID, uuid4

import duckdb
import pyarrow as pa
from sqlglot import exp, parse_one

from nats.errors import TimeoutError as NatsTimeoutError

from config import get_float, get_int, get_str
from config.performance import CRAWL_ACQUISITION_LANES, GRAPH_ACK_WAIT_SECONDS
from db.session import session_scope
from repository.catalogue import catalogue_from_env
from repository.catalogue.query import prepare_catalogue_query
from repository.ingestion.health import HealthMonitor
from repository.exceptions import RepositoryObjectNotFound
from repository.objects.config import object_store_from_env
from repository.objects.html import RawHtmlRepository, html_object_key
from runtime.catalogue_lane import catalogue_operation_lane
from runtime.edge_sql import edge_uses_catalogue
from runtime.graph_queue import (
    EDGE_CONSUMER,
    EDGE_SUBJECT,
    GRAPH_STREAM,
    NAVIGATION_READINESS_CONSUMER,
    NAVIGATION_READINESS_SUBJECT,
    EdgeWork,
    NavigationReadinessWork,
    edge_evaluation_identity,
    ensure_graph_progress_storage,
    ensure_graph_storage,
    list_graph_runs,
    update_edge_evaluation,
)
from runtime.nats_client import connect_nats
from runtime.graph_runs import (
    EdgeEvaluationBusy,
    EdgeEvaluationFailed,
    evaluate_edge,
    handle_navigation_readiness,
    resolve_policy_snapshot,
)
from runtime.navigation import (
    build_navigation_package,
    delete_run_navigation,
    load_navigation_package,
    put_navigation_package,
)
from runtime.navigation_contract import NavigationPackage
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

    def __init__(
        self,
        object_store,
        package: NavigationPackage,
        *,
        catalogue_snapshot_id: int | None = None,
    ) -> None:
        self._lock = Lock()
        self._connection = None
        self._object_store = object_store
        self._package = package
        self._catalogue_snapshot_id = catalogue_snapshot_id

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
        crawl_id = bound.get("crawl_id")
        if crawl_id is None:
            raise ValueError("Edge SQL requires its crawl_id parameter.")
        crawl_id = str(UUID(str(crawl_id)))
        bound["crawl_id"] = crawl_id
        table = pa.ipc.open_file(pa.BufferReader(navigation_payload)).read_all()
        table = table.append_column(
            "crawl_id", pa.array([crawl_id] * table.num_rows, type=pa.string())
        )
        statement = parse_one(sql, dialect="duckdb")
        for source in statement.find_all(exp.Table):
            if (
                source.db.lower() == "edge"
                and source.name.lower() == "page_links"
            ):
                source.set("db", None)
                source.set("this", exp.to_identifier("atlas_navigation_links"))

        if not edge_uses_catalogue(sql):
            with duckdb.connect(":memory:") as connection:
                with self._lock:
                    self._connection = connection
                try:
                    connection.execute(
                        "SET memory_limit = ?",
                        [get_str("ATLAS_EDGE_QUERY_MEMORY_LIMIT")],
                    )
                    connection.register("atlas_navigation_links", table)
                    reader = connection.execute(
                        statement.sql(dialect="duckdb"), bound
                    ).to_arrow_reader(batch_size=65_536)
                    return self._collect_urls(reader)
                finally:
                    with self._lock:
                        self._connection = None

        if self._catalogue_snapshot_id is None:
            raise ValueError(
                "catalogue edge SQL requires the graph run's pinned snapshot"
            )
        with catalogue_from_env() as catalogue:
            with self._lock:
                self._connection = catalogue.connection
            try:
                catalogue.connection.execute(
                    "SET memory_limit = ?", [get_str("ATLAS_EDGE_QUERY_MEMORY_LIMIT")]
                )
                catalogue.connection.register("atlas_navigation_links", table)
                prepared = prepare_catalogue_query(
                    catalogue, statement.sql(dialect="duckdb"), bound
                )
                prepared_statement = parse_one(
                    prepared.sql, dialect="duckdb"
                )
                self._pin_catalogue_sources(catalogue, prepared_statement)
                catalogue.connection.execute(f"USE {prepared.namespace}")
                reader = catalogue.connection.execute(
                    prepared_statement.sql(dialect="duckdb"),
                    prepared.bindings,
                ).to_arrow_reader(batch_size=65_536)
                return self._collect_urls(reader)
            finally:
                with self._lock:
                    self._connection = None

    def _pin_catalogue_sources(self, catalogue, statement: exp.Expression) -> None:
        cte_names = {
            cte.alias_or_name.lower()
            for cte in statement.find_all(exp.CTE)
            if cte.alias_or_name
        }
        sources = [
            source
            for source in statement.find_all(exp.Table)
            if source.name != "atlas_navigation_links"
            and not (
                not source.db and source.name.lower() in cte_names
            )
        ]
        for ordinal, source in enumerate(sources):
            original = source.copy()
            original.set("alias", None)
            if not original.db:
                original.set("db", exp.to_identifier(catalogue.config.schema))
            if not original.catalog:
                original.set(
                    "catalog", exp.to_identifier(catalogue.config.alias)
                )
            view_name = f"atlas_edge_snapshot_{ordinal}"
            catalogue.connection.execute(
                f"CREATE OR REPLACE TEMP VIEW {view_name} AS "
                f"SELECT * FROM {original.sql(dialect='duckdb')} "
                f"AT (VERSION => {self._catalogue_snapshot_id})"
            )
            source.set("catalog", exp.to_identifier("temp"))
            source.set("db", exp.to_identifier("main"))
            source.set("this", exp.to_identifier(view_name))

    @staticmethod
    def _collect_urls(reader) -> list[str]:
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

    def _load_or_regenerate(self, *, document_id: str, page_url: str) -> bytes:
        try:
            return load_navigation_package(self._object_store, self._package)
        except RepositoryObjectNotFound:
            document_hash = document_id.removeprefix("sha256:")
            html = RawHtmlRepository(self._object_store).read(
                html_object_key(document_hash)
            )
            payload, row_count = build_navigation_package(
                html,
                document_id=document_id,
                page_url=page_url,
            )
            rebuilt = put_navigation_package(
                self._object_store,
                name=self._package.object_name,
                payload=payload,
                row_count=row_count,
            )
            if rebuilt != self._package:
                self._object_store.delete(self._package.object_name)
                raise RuntimeError(
                    "regenerated navigation package does not match its NATS reference"
                )
            return payload


async def _process_navigation_readiness(
    message, runs, requests, progress, jetstream
) -> None:
    try:
        wakeup = NavigationReadinessWork.model_validate_json(message.data)
    except Exception:
        logging.exception("discarding invalid navigation-readiness work")
        await message.term()
        return
    try:
        await handle_navigation_readiness(
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
    object_store,
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
        interval = max(1.0, GRAPH_ACK_WAIT_SECONDS / 3)
        while True:
            await asyncio.sleep(interval)
            await message.in_progress()
            expires_at = datetime.now(UTC) + timedelta(
                seconds=GRAPH_ACK_WAIT_SECONDS * 2
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
        resource_request = (
            catalogue_request(
                f"edge:{identity}",
                service_class="critical",
                object_read_units=1,
            )
            if work.catalogue_snapshot_id is not None
            else object_request(
                f"edge-navigation:{identity}",
                direction="read",
                byte_count=work.navigation.byte_size,
                service_class="critical",
            )
        )
        async def execute() -> None:
            async with resource_permits(
                resource_grants,
                resource_request,
                acquire_timeout=DURABLE_RESOURCE_WAIT,
            ):
                with session_scope() as session:
                    await evaluate_edge(
                        runs=runs,
                        requests=requests,
                        progress=progress,
                        jetstream=jetstream,
                        work=work,
                        execute_urls=EdgeUrlExecutor(
                            object_store,
                            work.navigation,
                            catalogue_snapshot_id=work.catalogue_snapshot_id,
                        ),
                        policy_resolver=lambda url: resolve_policy_snapshot(session, url),
                        claim_token=claim_token,
                    )
        if work.catalogue_snapshot_id is None:
            await execute()
        else:
            async with catalogue_operation_lock:
                await execute()
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
    object_store = object_store_from_env()
    navigation_readiness = await jetstream.pull_subscribe(
        NAVIGATION_READINESS_SUBJECT,
        durable=NAVIGATION_READINESS_CONSUMER,
        stream=GRAPH_STREAM,
    )
    edges = await jetstream.pull_subscribe(
        EDGE_SUBJECT, durable=EDGE_CONSUMER, stream=GRAPH_STREAM
    )
    if monitor is not None:
        monitor.subsystem_ready("navigation")
    capacity = CRAWL_ACQUISITION_LANES
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
                                    object_store,
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
            for subscription, processor in (
                (navigation_readiness, _process_navigation_readiness),
                (edges, _process_edge),
            ):
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
                            object_store,
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
