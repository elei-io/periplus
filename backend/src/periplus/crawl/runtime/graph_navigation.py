"""Acquisition-owned readiness and bounded SQL-edge execution."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from hashlib import sha256
import logging
import time
from datetime import UTC, datetime, timedelta
from threading import Lock
from uuid import UUID, uuid4

import duckdb
import pyarrow as pa
from sqlglot import exp, parse_one

from nats.errors import TimeoutError as NatsTimeoutError

from periplus.platform.config import get_float, get_int, get_str
from periplus.platform.config.performance import CRAWL_ACQUISITION_LANES, GRAPH_ACK_WAIT_SECONDS
from periplus.platform.health import HealthMonitor
from periplus.ingestion.objects.exceptions import RepositoryObjectNotFound
from periplus.ingestion.objects.config import object_store_from_env
from periplus.ingestion.objects.html import RawHtmlRepository, html_object_key
from periplus.crawl.runtime.graph_queue import (
    EDGE_CONSUMER,
    EDGE_SUBJECT,
    GRAPH_STREAM,
    NAVIGATION_READINESS_CONSUMER,
    NAVIGATION_READINESS_SUBJECT,
    EdgeWork,
    NavigationReadinessWork,
    edge_evaluation_identity,
    ensure_graph_storage,
    get_crawl_request,
    get_edge_evaluation,
    get_graph_run,
    list_graph_runs,
    update_edge_evaluation,
)
from periplus.platform.messaging.client import connect_nats
from periplus.crawl.runtime.graph_runs import (
    DatabasePolicySnapshotResolver,
    EdgeEvaluationBusy,
    EdgeEvaluationDeferred,
    EdgeEvaluationFailed,
    EdgeEvaluationRetryable,
    evaluate_edge,
    handle_navigation_readiness,
)
from periplus.crawl.runtime.navigation import (
    build_edge_selection_package,
    build_navigation_package,
    delete_run_navigation,
    edge_selection_object_name,
    load_edge_selection_package,
    load_navigation_package,
    put_edge_selection_package,
    put_navigation_package,
)
from periplus.crawl.runtime.navigation_contract import EdgeSelectionPackage, NavigationPackage


class EdgeResultCache:
    """Bounded process-local reuse for deterministic edge query results."""

    def __init__(self, *, maximum_bytes: int) -> None:
        if maximum_bytes < 1:
            raise ValueError("edge-result cache size must be positive")
        self._maximum_bytes = maximum_bytes
        self._bytes = 0
        self._values: OrderedDict[str, tuple[tuple[str, ...], int]] = OrderedDict()
        self._lock = Lock()

    def get(self, identity: str) -> tuple[str, ...] | None:
        with self._lock:
            cached = self._values.pop(identity, None)
            if cached is None:
                return None
            self._values[identity] = cached
            return cached[0]

    def put(self, identity: str, urls: tuple[str, ...]) -> None:
        size = sum(len(url.encode()) + 8 for url in urls)
        if size > self._maximum_bytes:
            return
        with self._lock:
            previous = self._values.pop(identity, None)
            if previous is not None:
                self._bytes -= previous[1]
            while self._values and self._bytes + size > self._maximum_bytes:
                _key, (_urls, evicted_size) = self._values.popitem(last=False)
                self._bytes -= evicted_size
            self._values[identity] = (urls, size)
            self._bytes += size

    def discard(self, identity: str) -> None:
        with self._lock:
            cached = self._values.pop(identity, None)
            if cached is not None:
                self._bytes -= cached[1]


class EdgeUrlExecutor:
    """Execute one bounded DuckDB edge query and make it interruptible."""

    def __init__(
        self,
        object_store,
        package: NavigationPackage,
        *,
        result_cache: EdgeResultCache | None = None,
        cache_key: str | None = None,
        selection: EdgeSelectionPackage | None = None,
    ) -> None:
        self._lock = Lock()
        self._connection = None
        self._object_store = object_store
        self._package = package
        self._result_cache = result_cache
        self._cache_key = cache_key
        self._selection = selection
        self._selected_urls: tuple[str, ...] | None = None

    @property
    def selected_urls(self) -> tuple[str, ...] | None:
        return self._selected_urls

    def interrupt(self) -> None:
        with self._lock:
            connection = self._connection
        if connection is not None:
            connection.interrupt()

    def __call__(self, sql: str, parameters: dict[str, object]) -> list[str]:
        if self._result_cache is not None and self._cache_key is not None:
            cached = self._result_cache.get(self._cache_key)
            if cached is not None:
                self._selected_urls = cached
                return list(cached)
        if self._selection is not None:
            selected = load_edge_selection_package(
                self._object_store,
                self._selection,
            )
            self._remember(selected)
            return list(selected)
        bound = dict(parameters)
        page_url = str(bound.pop("_page_url"))
        content_sha256 = str(bound.pop("_content_sha256"))
        navigation_payload = self._load_or_regenerate(
            content_sha256=content_sha256,
            page_url=page_url,
        )
        crawl_id = bound.pop("crawl_id", None)
        if crawl_id is None:
            raise ValueError("Edge evaluation requires a crawl_id.")
        crawl_id = str(UUID(str(crawl_id)))
        bound["crawl_id"] = crawl_id
        table = pa.ipc.open_file(pa.BufferReader(navigation_payload)).read_all()
        table = table.append_column(
            "crawl_id", pa.array([crawl_id] * table.num_rows, type=pa.string())
        )
        statement = self._prepare_statement(sql)
        with duckdb.connect(":memory:") as connection:
            with self._lock:
                self._connection = connection
            try:
                connection.execute(
                    "SET memory_limit = ?",
                    [get_str("PERIPLUS_EDGE_QUERY_MEMORY_LIMIT")],
                )
                connection.execute("SET enable_external_access = false")
                connection.execute("SET autoinstall_known_extensions = false")
                connection.execute("SET autoload_known_extensions = false")
                connection.register("periplus_navigation_links", table)
                connection.execute("CREATE SCHEMA nav")
                connection.execute(
                    "CREATE VIEW nav.links AS "
                    "SELECT * FROM periplus_navigation_links"
                )
                connection.execute("SET lock_configuration = true")
                reader = connection.execute(
                    statement.sql(dialect="duckdb")
                ).to_arrow_reader(batch_size=65_536)
                urls = self._collect_urls(reader)
            finally:
                with self._lock:
                    self._connection = None
        self._remember(tuple(urls))
        return urls

    @staticmethod
    def _prepare_statement(sql: str) -> exp.Query:
        statement = parse_one(sql, dialect="duckdb")
        if not isinstance(statement, exp.Query):
            raise ValueError("Compiled edge SQL must be a query.")
        return statement

    def _remember(self, urls: tuple[str, ...]) -> None:
        self._selected_urls = urls
        if self._result_cache is not None and self._cache_key is not None:
            self._result_cache.put(self._cache_key, urls)

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
            if rows > get_int("PERIPLUS_EDGE_MAX_OUTPUT_ROWS"):
                raise ValueError("Edge SQL exceeded its row limit.")
            if output_bytes > get_int("PERIPLUS_EDGE_MAX_OUTPUT_BYTES"):
                raise ValueError("Edge SQL exceeded its output-byte limit.")
            urls.extend(
                str(value)
                for value in batch.column(index).to_pylist()
                if value is not None
            )
        return urls

    def _load_or_regenerate(self, *, content_sha256: str, page_url: str) -> bytes:
        try:
            return load_navigation_package(self._object_store, self._package)
        except RepositoryObjectNotFound:
            content_hash = content_sha256.removeprefix("sha256:")
            html = RawHtmlRepository(self._object_store).read(
                html_object_key(content_hash)
            )
            payload, row_count = build_navigation_package(
                html,
                content_sha256=content_sha256,
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


async def _retain_deferred_edge_selection(
    *,
    requests,
    object_store,
    work: EdgeWork,
    identity: str,
    executor: EdgeUrlExecutor,
) -> None:
    urls = executor.selected_urls
    if urls is None:
        return
    current = await get_edge_evaluation(requests, identity)
    if current is None or current.selection is not None:
        return
    payload = await asyncio.to_thread(build_edge_selection_package, urls)
    package = await asyncio.to_thread(
        put_edge_selection_package,
        object_store,
        name=edge_selection_object_name(
            work.graph_run_id,
            identity,
            sha256(payload).hexdigest(),
        ),
        payload=payload,
        row_count=len(urls),
    )
    await update_edge_evaluation(
        requests,
        identity,
        lambda value: (
            value
            if value.selection is not None
            else value.model_copy(
                update={
                    "selection": package,
                    "updated_at": datetime.now(UTC),
                }
            )
        ),
    )


async def _process_edge(
    message,
    runs,
    requests,
    progress,
    jetstream,
    object_store,
    result_cache: EdgeResultCache,
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
    request = await get_crawl_request(requests, work.crawl_request_id)
    if request is None or request.generation != work.generation:
        await message.ack()
        return
    run = await get_graph_run(runs, work.graph_run_id)
    if run is None or run.status in {
        "completed",
        "completed_with_errors",
        "failed",
        "cancelled",
    }:
        await message.ack()
        return
    if run.status == "paused":
        await message.nak(delay=30)
        return
    evaluation = await get_edge_evaluation(requests, identity)
    selection = evaluation.selection if evaluation is not None else None
    executor = EdgeUrlExecutor(
        object_store,
        work.navigation,
        result_cache=result_cache,
        cache_key=identity,
        selection=selection,
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
        await evaluate_edge(
            runs=runs,
            requests=requests,
            progress=progress,
            jetstream=jetstream,
            work=work,
            execute_urls=executor,
            policy_resolver=DatabasePolicySnapshotResolver(),
            claim_token=claim_token,
        )
    except EdgeEvaluationBusy:
        await message.nak(delay=1)
        return
    except EdgeEvaluationDeferred:
        try:
            await _retain_deferred_edge_selection(
                requests=requests,
                object_store=object_store,
                work=work,
                identity=identity,
                executor=executor,
            )
        except Exception:
            logging.warning(
                "failed to retain deferred edge selection %s",
                identity,
                exc_info=True,
            )
        await message.nak(delay=1)
        return
    except EdgeEvaluationRetryable:
        await message.nak(delay=1)
        return
    except EdgeEvaluationFailed:
        result_cache.discard(identity)
        await message.ack()
        return
    except Exception:
        await message.nak(delay=1)
        return
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
    # Evaluation records terminal semantic failures itself; redelivery cannot repair SQL.
    result_cache.discard(identity)
    await message.ack()


async def run(monitor: HealthMonitor | None = None) -> None:
    stop = asyncio.Event()
    client = await connect_nats()
    jetstream = client.jetstream()
    runs, requests, _workers = await ensure_graph_storage(jetstream)
    progress = None
    capacity = CRAWL_ACQUISITION_LANES
    object_store = object_store_from_env(maximum_concurrency=capacity)
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
    result_cache = EdgeResultCache(
        maximum_bytes=get_int("PERIPLUS_EDGE_MAX_OUTPUT_BYTES") * 2
    )
    active: set[asyncio.Task] = set()
    pull_sources = (
        (navigation_readiness, _process_navigation_readiness),
        (edges, _process_edge),
    )
    pulls: dict[object, tuple[asyncio.Task, int]] = {}
    cleaned_runs = set()
    next_cleanup = 0.0
    try:
        while not stop.is_set():
            if time.monotonic() >= next_cleanup:
                now = datetime.now(UTC)
                grace = get_float("PERIPLUS_NAVIGATION_CLEANUP_GRACE_SECONDS")
                for graph_run in await list_graph_runs(runs):
                    if (
                        graph_run.id not in cleaned_runs
                        and graph_run.completed_at is not None
                        and (now - graph_run.completed_at).total_seconds() >= grace
                    ):
                        try:
                            await asyncio.to_thread(
                                delete_run_navigation,
                                object_store,
                                graph_run.id,
                            )
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
            fetched = False
            for subscription, processor in pull_sources:
                pending = pulls.get(processor)
                if pending is None or not pending[0].done():
                    continue
                task, _reserved = pulls.pop(processor)
                try:
                    messages = task.result()
                except (NatsTimeoutError, asyncio.TimeoutError):
                    continue
                fetched = fetched or bool(messages)
                for message in messages:
                    arguments = (message, runs, requests, progress, jetstream)
                    if processor is _process_edge:
                        arguments += (
                            object_store,
                            result_cache,
                        )
                    active.add(asyncio.create_task(processor(*arguments)))
            reserved = sum(batch for _task, batch in pulls.values())
            available = capacity - len(active) - reserved
            missing = [
                (subscription, processor)
                for subscription, processor in pull_sources
                if processor not in pulls
            ]
            for index, (subscription, processor) in enumerate(missing):
                if available <= 0:
                    break
                batch = max(1, available // (len(missing) - index))
                task = asyncio.create_task(
                    subscription.fetch(batch=batch, timeout=60),
                    name=f"navigation-{processor.__name__}-pull",
                )
                pulls[processor] = (task, batch)
                available -= batch
            if not fetched:
                waiters = {
                    *active,
                    *(task for task, _batch in pulls.values()),
                }
                if waiters:
                    await asyncio.wait(
                        waiters,
                        timeout=0.05,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                else:
                    await asyncio.sleep(0.05)
    finally:
        stop.set()
        for task, _batch in pulls.values():
            task.cancel()
        await asyncio.gather(
            *active,
            *(task for task, _batch in pulls.values()),
            return_exceptions=True,
        )
        await client.drain()
