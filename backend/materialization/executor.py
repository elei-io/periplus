"""Fixed CDC-driven rebuildable catalogue materializations."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
import json
import logging
from typing import Callable, Literal
from urllib.parse import urlsplit
from uuid import UUID

import pyarrow as pa
from nats.errors import TimeoutError as NatsTimeoutError
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy
import tldextract

from config.performance import (
    MATERIALIZATION_QUACK_CLIENTS,
    materialization_duckdb_memory_limit,
)
from control.urls import normalize_url
from dom import (
    ElementRow,
    iter_html_byte_elements,
    iter_html_elements,
    links_from_elements,
)
from repository.catalogue import Catalogue, catalogue_from_env, page_id_for
from repository.objects.config import object_store_from_env
from repository.objects.document import ExactDocumentRepository
from repository.objects.html import RawHtmlRepository
from repository.ingestion.health import HealthMonitor
from cdc.events import (
    DMLTick,
    EVENT_STREAM,
    dml_subject,
    ensure_cdc_stream,
)
from runtime.nats_client import connect_nats
from runtime.catalogue_workers import CatalogueLaneReporter
from runtime.operation_leases import (
    OperationLeaseLost,
    OperationLeaseUnavailable,
    ensure_operation_lease_storage,
    operation_leases,
)


WorkloadName = Literal[
    "html_elements",
    "jsonld_values",
    "pages",
    "page_observations",
    "links",
]
_TLD_EXTRACT = tldextract.TLDExtract(suffix_list_urls=())
_ACK_WAIT_SECONDS = 300
_FETCH_BATCH = 100
_FETCH_TIMEOUT_SECONDS = 60
_COALESCE_TIMEOUT_SECONDS = 0.1
_HTML_RECONCILE_BATCH = 100
_MERGE_BATCH = 250


@dataclass(frozen=True, slots=True)
class Workload:
    name: WorkloadName
    source_schema: str
    source_table: str
    target_table: str
    refresh: Callable[[Catalogue, RawHtmlRepository], None]

    @property
    def durable(self) -> str:
        return f"atlas-material-{self.name}-v1"


@dataclass(slots=True)
class MaterializationLane:
    """Keep one DuckBasin session on the one thread that created it."""

    catalogue: Catalogue
    executor: ThreadPoolExecutor

    @classmethod
    async def open(cls, index: int) -> MaterializationLane:
        executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=f"material-{index}",
        )
        try:
            catalogue = await asyncio.get_running_loop().run_in_executor(
                executor,
                lambda: catalogue_from_env(
                    memory_limit=materialization_duckdb_memory_limit()
                ),
            )
        except BaseException:
            executor.shutdown(wait=True, cancel_futures=True)
            raise
        return cls(catalogue=catalogue, executor=executor)

    async def call(self, operation, *args):
        return await asyncio.get_running_loop().run_in_executor(
            self.executor,
            operation,
            self.catalogue,
            *args,
        )

    async def close(self) -> None:
        try:
            await asyncio.get_running_loop().run_in_executor(
                self.executor,
                self.catalogue.close,
            )
        finally:
            self.executor.shutdown(wait=True, cancel_futures=True)


@dataclass(slots=True)
class LinkObservation:
    relation_scope: str
    first_seen_at: datetime
    last_seen_at: datetime

    def observe(self, observed_at: datetime) -> None:
        self.first_seen_at = min(self.first_seen_at, observed_at)
        self.last_seen_at = max(self.last_seen_at, observed_at)


@dataclass(slots=True)
class ObservationRange:
    first_seen_at: datetime
    last_seen_at: datetime

    def observe(self, observed_at: datetime) -> None:
        self.first_seen_at = min(self.first_seen_at, observed_at)
        self.last_seen_at = max(self.last_seen_at, observed_at)


class MaterializationDependencyNotReady(RuntimeError):
    """A source delta is committed but its derived dependency is not."""


def workloads() -> tuple[Workload, ...]:
    return (
        Workload(
            "html_elements",
            "ingest",
            "documents",
            "html_elements",
            _reconcile_html_elements,
        ),
        Workload(
            "jsonld_values",
            "material",
            "html_elements",
            "jsonld_values",
            _refresh_jsonld_values,
        ),
        Workload(
            "pages",
            "ingest",
            "visits",
            "pages",
            _refresh_pages,
        ),
        Workload(
            "page_observations",
            "ingest",
            "visits",
            "page_observations",
            _refresh_page_observations,
        ),
        Workload(
            "links",
            "ingest",
            "documents",
            "links",
            _refresh_links,
        ),
    )


async def run(
    *,
    stop: asyncio.Event | None = None,
    monitor: HealthMonitor | None = None,
    lanes: tuple[CatalogueLaneReporter, ...] | None = None,
) -> None:
    stop = stop or asyncio.Event()
    lane_reporters = lanes or tuple(
        CatalogueLaneReporter(lane_index=index)
        for index in range(MATERIALIZATION_QUACK_CLIENTS)
    )
    if len(lane_reporters) != MATERIALIZATION_QUACK_CLIENTS:
        raise ValueError(
            "materialization lane reporters must match configured client lanes"
        )

    client = await connect_nats()
    materialization_lanes: list[MaterializationLane] = []
    try:
        jetstream = client.jetstream()
        await ensure_cdc_stream(jetstream)
        leases = await ensure_operation_lease_storage(jetstream)
        for index in range(MATERIALIZATION_QUACK_CLIENTS):
            materialization_lanes.append(await MaterializationLane.open(index))
        html_repository = RawHtmlRepository(
            object_store_from_env(maximum_concurrency=4)
        )
        for reporter in lane_reporters:
            reporter.attach(lambda: (True, "ready"))
        if monitor is not None:
            monitor.dependencies_ready()
            monitor.subsystem_ready("materialization")

        tasks = [
            asyncio.create_task(
                _run_workload(
                    jetstream,
                    leases,
                    materialization_lanes[index],
                    html_repository,
                    workload,
                    stop,
                    monitor,
                    lane_reporters[index],
                ),
                name=f"materialization-{workload.name}",
            )
            for index, workload in enumerate(workloads())
        ]
        await asyncio.gather(*tasks)
    finally:
        await asyncio.gather(
            *(lane.close() for lane in materialization_lanes),
            return_exceptions=True,
        )
        await client.close()


async def _run_workload(
    jetstream,
    leases,
    materialization_lane: MaterializationLane,
    html_repository: RawHtmlRepository,
    workload: Workload,
    stop: asyncio.Event,
    monitor: HealthMonitor | None,
    lane: CatalogueLaneReporter,
) -> None:
    table_uuid = await materialization_lane.call(
        _table_uuid,
        workload.source_schema,
        workload.source_table,
    )
    subject = dml_subject(table_uuid)
    subscription = await jetstream.pull_subscribe(
        subject,
        durable=workload.durable,
        stream=EVENT_STREAM,
        config=ConsumerConfig(
            durable_name=workload.durable,
            deliver_policy=DeliverPolicy.ALL,
            ack_policy=AckPolicy.EXPLICIT,
            ack_wait=_ACK_WAIT_SECONDS,
            max_ack_pending=_FETCH_BATCH,
            filter_subject=subject,
        ),
    )

    consumer_info = await subscription.consumer_info()
    if _requires_startup_backfill(workload, consumer_info):
        await _bootstrap_workload(
            leases,
            workload,
            materialization_lane,
            html_repository,
            stop,
        )
    else:
        logging.info(
            "resuming %s materialization from durable consumer sequence %s",
            workload.name,
            consumer_info.delivered.consumer_seq,
        )
    while not stop.is_set():
        if monitor is not None:
            monitor.heartbeat()
        try:
            messages = await subscription.fetch(
                batch=_FETCH_BATCH, timeout=_FETCH_TIMEOUT_SECONDS
            )
        except (NatsTimeoutError, TimeoutError):
            continue
        if not messages:
            continue
        while len(messages) < _FETCH_BATCH:
            try:
                additional = await subscription.fetch(
                    batch=_FETCH_BATCH - len(messages),
                    timeout=_COALESCE_TIMEOUT_SECONDS,
                )
            except (NatsTimeoutError, TimeoutError):
                break
            if not additional:
                break
            messages.extend(additional)
        await _process_tick_batch(
            leases,
            workload,
            materialization_lane,
            html_repository,
            lane,
            messages,
        )


async def _bootstrap_workload(
    leases,
    workload: Workload,
    lane: MaterializationLane,
    html_repository: RawHtmlRepository,
    stop: asyncio.Event,
) -> None:
    """Reconcile or backfill before consuming CDC, retrying dependencies."""

    while not stop.is_set():
        try:
            await _refresh_with_lease(
                leases,
                workload,
                lane,
                html_repository,
                acquire_timeout=1,
            )
            return
        except (OperationLeaseUnavailable, OperationLeaseLost):
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception(
                "fixed materialization %s startup bootstrap failed; retrying",
                workload.name,
            )
            await asyncio.sleep(1)


def _requires_startup_backfill(workload: Workload, consumer_info) -> bool:
    if workload.name == "html_elements":
        return True
    return consumer_info.delivered.consumer_seq == 0


async def _process_tick_batch(
    leases,
    workload: Workload,
    materialization_lane: MaterializationLane,
    html_repository: RawHtmlRepository,
    lane_reporter: CatalogueLaneReporter,
    messages,
) -> None:
    """Coalesce source ticks into one committed materialization operation."""

    ticks = [DMLTick.model_validate_json(msg.data) for msg in messages]
    lane_reporter.active_operation_count += 1
    try:
        await _refresh_with_lease(
            leases,
            workload,
            materialization_lane,
            html_repository,
            acquire_timeout=0,
            ticks=tuple(ticks),
        )
    except (OperationLeaseUnavailable, OperationLeaseLost):
        for message in messages:
            await message.nak(delay=1)
        return
    except MaterializationDependencyNotReady as exc:
        logging.info("%s; retrying materialization batch", exc)
        for message in messages:
            await message.nak(delay=1)
        return
    except asyncio.CancelledError:
        raise
    except Exception:
        logging.exception(
            "fixed materialization %s failed through snapshot %s",
            workload.name,
            max(tick.snapshot_id for tick in ticks),
        )
        for message in messages:
            await message.nak()
        return
    finally:
        lane_reporter.active_operation_count -= 1
    for message in messages:
        await message.ack()


async def _refresh_with_lease(
    leases,
    workload: Workload,
    lane: MaterializationLane,
    html_repository: RawHtmlRepository,
    *,
    acquire_timeout: float,
    ticks: tuple[DMLTick, ...] | None = None,
) -> None:
    async with operation_leases(
        leases,
        (f"material.{workload.target_table}",),
        phase="materialization",
        acquire_timeout=acquire_timeout,
    ):
        if ticks is None:
            await lane.call(workload.refresh, html_repository)
            return
        incremental = {
            "html_elements": _refresh_html_elements_incremental,
            "jsonld_values": _refresh_jsonld_values_incremental,
            "pages": _refresh_pages_incremental,
            "page_observations": _refresh_page_observations_incremental,
            "links": _refresh_links_incremental,
        }[workload.name]
        await lane.call(incremental, html_repository, ticks)


def _table_uuid(
    catalogue: Catalogue, schema_name: str, table_name: str
) -> UUID:
    rows = catalogue.trusted_remote_rows(
        f"""
        SELECT lake_table.table_uuid
        FROM ducklake_table_info('{catalogue.config.alias}') AS lake_table
        JOIN information_schema.tables AS names
          ON names.table_name = lake_table.table_name
        WHERE names.table_catalog = '{catalogue.config.alias}'
          AND names.table_schema = '{schema_name}'
          AND names.table_name = '{table_name}'
        """
    )
    if len(rows) != 1:
        raise RuntimeError(
            f"expected exactly one source table {schema_name}.{table_name}"
        )
    return UUID(str(rows[0][0]))


def _html_documents(
    catalogue: Catalogue,
    content_hashes: set[str] | None = None,
) -> list[tuple[str, str, str]]:
    hash_filter = ""
    if content_hashes is not None:
        if not content_hashes:
            return []
        hash_filter = (
            "\n          AND content_sha256 IN "
            f"({_sql_string_list(content_hashes)})"
        )
    rows = catalogue.trusted_remote_rows(
        f"""
        SELECT content_sha256, object_key, storage_encoding
        FROM ingest.documents
        WHERE lower(detected_media_type) = 'text/html'
        {hash_filter}
        ORDER BY
          content_sha256,
          CASE storage_encoding WHEN 'zstd' THEN 0 ELSE 1 END,
          object_key
        """
    )
    documents: dict[str, tuple[str, str, str]] = {}
    for content_hash, object_key, storage_encoding in rows:
        value = str(content_hash)
        documents.setdefault(
            value,
            (
                value,
                str(object_key),
                str(storage_encoding),
            ),
        )
    return list(documents.values())


def _project_html_documents(
    html_repository: RawHtmlRepository,
    documents: list[tuple[str, str, str]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    exact_repository = ExactDocumentRepository(html_repository.store)
    for (
        content_sha256,
        object_key,
        storage_encoding,
    ) in documents:
        if storage_encoding == "zstd":
            html: str | bytes = html_repository.read(object_key)
        elif storage_encoding == "identity":
            html = exact_repository.read_bytes(object_key)
        else:
            raise ValueError(
                f"unsupported HTML storage encoding {storage_encoding!r}"
            )
        elements = (
            iter_html_byte_elements(html)
            if isinstance(html, bytes)
            else iter_html_elements(html)
        )
        for element in elements:
            rows.append(
                {
                    "content_sha256": content_sha256,
                    "element_index": element.element_index,
                    "parent_index": element.parent_index,
                    "subtree_end_index": element.subtree_end_index,
                    "depth": element.depth,
                    "child_index": element.child_index,
                    "tag": element.tag.lower(),
                    "namespace": _namespace_name(element.namespace_uri),
                    "attributes": element.attributes,
                    "text_direct": element.text_direct,
                    "text_tail": element.text_tail,
                }
            )
    return rows


def _refresh_html_elements_incremental(
    catalogue: Catalogue,
    html_repository: RawHtmlRepository,
    ticks: tuple[DMLTick, ...],
) -> None:
    start_snapshot = min(tick.start_snapshot for tick in ticks)
    end_snapshot = max(tick.end_snapshot for tick in ticks)
    affected_hashes = _changed_html_hashes(
        catalogue,
        start_snapshot=start_snapshot,
        end_snapshot=end_snapshot,
    )
    projected, removed = _materialize_html_hashes(
        catalogue,
        html_repository,
        affected_hashes,
    )
    logging.info(
        "incremental html_elements snapshots %s-%s "
        "affected_hashes=%s projected_hashes=%s removed_hashes=%s",
        start_snapshot,
        end_snapshot,
        len(affected_hashes),
        projected,
        removed,
    )


def _changed_html_hashes(
    catalogue: Catalogue,
    *,
    start_snapshot: int,
    end_snapshot: int,
) -> set[str]:
    alias = _sql_string(catalogue.config.alias)
    rows = catalogue.trusted_remote_rows(
        f"""
        SELECT DISTINCT content_sha256
        FROM ducklake_table_changes(
          {alias}, 'ingest', 'documents',
          {start_snapshot}, {end_snapshot}
        )
        WHERE lower(detected_media_type) = 'text/html'
          AND content_sha256 IS NOT NULL
        """
    )
    return {str(content_hash) for (content_hash,) in rows}


def _materialize_html_hashes(
    catalogue: Catalogue,
    html_repository: RawHtmlRepository,
    affected_hashes: set[str],
) -> tuple[int, int]:
    if not affected_hashes:
        return 0, 0
    documents = _html_documents(catalogue, affected_hashes)
    live_documents = {
        content_hash: (content_hash, object_key, storage_encoding)
        for content_hash, object_key, storage_encoding in documents
    }
    covered_hashes = _covered_html_hashes(catalogue, affected_hashes)
    missing_hashes = live_documents.keys() - covered_hashes
    removed_hashes = covered_hashes - live_documents.keys()
    missing_documents = [
        live_documents[content_hash]
        for content_hash in sorted(missing_hashes)
    ]
    rows = _project_html_documents(html_repository, missing_documents)
    _apply_html_element_delta(
        catalogue,
        rows=rows,
        removed_hashes=set(removed_hashes),
    )
    return len(missing_documents), len(removed_hashes)


def _covered_html_hashes(
    catalogue: Catalogue,
    content_hashes: set[str],
) -> set[str]:
    if not content_hashes:
        return set()
    rows = catalogue.trusted_remote_rows(
        f"""
        SELECT DISTINCT content_sha256
        FROM material.html_elements
        WHERE content_sha256 IN ({_sql_string_list(content_hashes)})
        """
    )
    return {str(content_hash) for (content_hash,) in rows}


def _reconcile_html_elements(
    catalogue: Catalogue,
    html_repository: RawHtmlRepository,
) -> None:
    projected = 0
    removed = 0
    while True:
        missing_documents = _missing_html_documents(catalogue)
        orphaned_hashes = _orphaned_html_hashes(catalogue)
        if not missing_documents and not orphaned_hashes:
            break
        rows = _project_html_documents(html_repository, missing_documents)
        _apply_html_element_delta(
            catalogue,
            rows=rows,
            removed_hashes=orphaned_hashes,
        )
        projected += len(missing_documents)
        removed += len(orphaned_hashes)
    logging.info(
        "reconciled html_elements projected_hashes=%s removed_hashes=%s",
        projected,
        removed,
    )


def _missing_html_documents(
    catalogue: Catalogue,
) -> list[tuple[str, str, str]]:
    rows = catalogue.trusted_remote_rows(
        f"""
        WITH candidates AS (
          SELECT content_sha256, object_key, storage_encoding,
                 row_number() OVER (
                   PARTITION BY content_sha256
                   ORDER BY
                     CASE storage_encoding WHEN 'zstd' THEN 0 ELSE 1 END,
                     object_key
                 ) AS candidate_index
          FROM ingest.documents
          WHERE lower(detected_media_type) = 'text/html'
        )
        SELECT content_sha256, object_key, storage_encoding
        FROM candidates
        WHERE candidate_index = 1
          AND NOT EXISTS (
            SELECT 1
            FROM material.html_elements AS elements
            WHERE elements.content_sha256 = candidates.content_sha256
          )
        ORDER BY content_sha256
        LIMIT {_HTML_RECONCILE_BATCH}
        """
    )
    return [
        (str(content_hash), str(object_key), str(storage_encoding))
        for content_hash, object_key, storage_encoding in rows
    ]


def _orphaned_html_hashes(catalogue: Catalogue) -> set[str]:
    rows = catalogue.trusted_remote_rows(
        f"""
        SELECT DISTINCT elements.content_sha256
        FROM material.html_elements AS elements
        WHERE NOT EXISTS (
          SELECT 1
          FROM ingest.documents AS documents
          WHERE documents.content_sha256 = elements.content_sha256
            AND lower(documents.detected_media_type) = 'text/html'
        )
        ORDER BY elements.content_sha256
        LIMIT {_HTML_RECONCILE_BATCH}
        """
    )
    return {str(content_hash) for (content_hash,) in rows}


def _apply_html_element_delta(
    catalogue: Catalogue,
    *,
    rows: list[dict[str, object]],
    removed_hashes: set[str],
) -> None:
    if not rows and not removed_hashes:
        return
    element_registration = "_atlas_material_html_elements_delta"
    if rows:
        catalogue.trusted_connection.register(
            element_registration,
            _arrow_table(rows, map_columns={"attributes"}),
        )
    try:
        with catalogue.remote_transaction():
            if removed_hashes:
                values = ", ".join(
                    f"({_sql_string(content_hash)})"
                    for content_hash in sorted(removed_hashes)
                )
                catalogue.trusted_remote_execute(
                    f"""
                    MERGE INTO material.html_elements AS target
                    USING (VALUES {values}) AS removals(content_sha256)
                      ON target.content_sha256 = removals.content_sha256
                    WHEN MATCHED THEN DELETE
                    """
                )
            if rows:
                catalogue.trusted_connection.execute(
                    """
                    INSERT INTO material.html_elements BY NAME
                    SELECT *
                    FROM _atlas_material_html_elements_delta
                    """
                )
    finally:
        if rows:
            catalogue.trusted_connection.unregister(element_registration)


def _refresh_jsonld_values(
    catalogue: Catalogue, _html_repository: RawHtmlRepository
) -> None:
    source = catalogue.trusted_remote_rows(
        f"""
        SELECT content_sha256, element_index, attributes, text_direct
        FROM material.html_elements
        WHERE tag = 'script'
        ORDER BY content_sha256, element_index
        """
    )
    _replace(
        catalogue,
        "jsonld_values",
        _jsonld_rows(source),
        variant_columns={"value"},
    )


def _refresh_jsonld_values_incremental(
    catalogue: Catalogue,
    _html_repository: RawHtmlRepository,
    ticks: tuple[DMLTick, ...],
) -> None:
    start_snapshot, end_snapshot = _snapshot_window(ticks)
    hashes = _changed_material_hashes(
        catalogue,
        table_name="html_elements",
        start_snapshot=start_snapshot,
        end_snapshot=end_snapshot,
    )
    if not hashes:
        return
    source = catalogue.trusted_remote_rows(
        f"""
        SELECT content_sha256, element_index, attributes, text_direct
        FROM material.html_elements
        WHERE content_sha256 IN ({_sql_string_list(hashes)})
          AND tag = 'script'
        ORDER BY content_sha256, element_index
        """
    )
    rows = _jsonld_rows(source)
    _replace_content_hash_slices(
        catalogue,
        table_name="jsonld_values",
        content_hashes=hashes,
        rows=rows,
        variant_columns={"value"},
    )
    logging.info(
        "incremental jsonld_values snapshots %s-%s "
        "affected_hashes=%s rows=%s",
        start_snapshot,
        end_snapshot,
        len(hashes),
        len(rows),
    )


def _jsonld_rows(source) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for content_sha256, element_index, attributes, text in source:
        attrs = dict(attributes or {})
        media_type = str(attrs.get("type") or "").split(";", 1)[0].strip().lower()
        if media_type != "application/ld+json":
            continue
        try:
            value = json.loads(str(text))
        except (TypeError, ValueError):
            continue
        rows.append(
            {
                "content_sha256": str(content_sha256),
                "element_index": int(element_index),
                "type_terms": sorted(_jsonld_type_terms(value)),
                "value": json.dumps(
                    value,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            }
        )
    return rows


def _refresh_pages(
    catalogue: Catalogue, _html_repository: RawHtmlRepository
) -> None:
    source = catalogue.trusted_remote_rows(
        f"""
        SELECT coalesce(effective_url, requested_url)
        FROM ingest.visits
        WHERE observed_at IS NOT NULL
        ORDER BY visit_id
        """
    )
    rows_by_url: dict[str, dict[str, object]] = {}
    for (raw_url,) in source:
        normalized = normalize_url(str(raw_url))
        rows_by_url[normalized] = _page_row(normalized)
    _replace(catalogue, "pages", list(rows_by_url.values()))


def _refresh_pages_incremental(
    catalogue: Catalogue,
    _html_repository: RawHtmlRepository,
    ticks: tuple[DMLTick, ...],
) -> None:
    start_snapshot, end_snapshot = _snapshot_window(ticks)
    alias = _sql_string(catalogue.config.alias)
    changed = catalogue.trusted_remote_rows(
        f"""
        SELECT coalesce(effective_url, requested_url)
        FROM ducklake_table_changes(
          {alias}, 'ingest', 'visits',
          {start_snapshot}, {end_snapshot}
        )
        WHERE observed_at IS NOT NULL
          AND change_type IN ('insert', 'update_postimage')
        """
    )
    normalized_urls = {
        normalize_url(str(raw_url))
        for (raw_url,) in changed
        if raw_url is not None
    }
    rows = [_page_row(url) for url in sorted(normalized_urls)]
    _merge_page_rows(catalogue, rows)
    logging.info(
        "incremental pages snapshots %s-%s affected_urls=%s inserted_candidates=%s",
        start_snapshot,
        end_snapshot,
        len(normalized_urls),
        len(rows),
    )


def _page_row(normalized_url: str) -> dict[str, object]:
    parsed = urlsplit(normalized_url)
    return {
        "page_id": str(page_id_for(normalized_url)),
        "normalized_url": normalized_url,
        "scheme": parsed.scheme,
        "hostname": parsed.hostname,
        "port": parsed.port,
        "path": parsed.path,
        "query": parsed.query or None,
        "registrable_domain": _registrable_domain(parsed.hostname or ""),
    }


def _refresh_page_observations(
    catalogue: Catalogue, _html_repository: RawHtmlRepository
) -> None:
    source = catalogue.trusted_remote_rows(
        """
        SELECT visit_id, document_id,
               coalesce(effective_url, requested_url), observed_at
        FROM ingest.visits
        WHERE observed_at IS NOT NULL
        ORDER BY visit_id
        """
    )
    _replace(
        catalogue,
        "page_observations",
        [_page_observation_row(*values) for values in source],
    )


def _refresh_page_observations_incremental(
    catalogue: Catalogue,
    _html_repository: RawHtmlRepository,
    ticks: tuple[DMLTick, ...],
) -> None:
    start_snapshot, end_snapshot = _snapshot_window(ticks)
    alias = _sql_string(catalogue.config.alias)
    changed = catalogue.trusted_remote_rows(
        f"""
        SELECT visit_id, document_id,
               coalesce(effective_url, requested_url), observed_at
        FROM ducklake_table_changes(
          {alias}, 'ingest', 'visits',
          {start_snapshot}, {end_snapshot}
        )
        WHERE observed_at IS NOT NULL
          AND change_type IN ('insert', 'update_postimage')
        """
    )
    rows_by_identity: dict[tuple[str, str], dict[str, object]] = {}
    for values in changed:
        row = _page_observation_row(*values)
        rows_by_identity[
            (str(row["page_id"]), str(row["visit_id"]))
        ] = row
    rows = [
        rows_by_identity[identity]
        for identity in sorted(rows_by_identity)
    ]
    _merge_page_observation_rows(catalogue, rows)
    logging.info(
        "incremental page_observations snapshots %s-%s observations=%s",
        start_snapshot,
        end_snapshot,
        len(rows),
    )


def _page_observation_row(
    visit_id: object,
    document_id: object,
    raw_url: object,
    observed_at: object,
) -> dict[str, object]:
    normalized_url = normalize_url(str(raw_url))
    return {
        "page_id": str(page_id_for(normalized_url)),
        "visit_id": str(visit_id),
        "document_id": str(document_id) if document_id is not None else None,
        "observed_at": observed_at,
    }


def _refresh_links(
    catalogue: Catalogue, _html_repository: RawHtmlRepository
) -> None:
    source = catalogue.trusted_remote_rows(
        f"""
        SELECT d.content_sha256,
               coalesce(v.effective_url, v.requested_url), v.observed_at
        FROM ingest.documents AS d
        JOIN ingest.visits AS v USING (document_id)
        WHERE lower(d.detected_media_type) = 'text/html'
        ORDER BY d.content_sha256, v.observed_at
        """
    )
    projected = catalogue.trusted_remote_rows(
        """
        SELECT content_sha256, element_index, parent_index,
               subtree_end_index, depth, child_index, tag, namespace,
               attributes, text_direct, text_tail
        FROM material.html_elements
        ORDER BY content_sha256, element_index
        """
    )
    parsed_content: dict[str, list[ElementRow]] = defaultdict(list)
    for values in projected:
        parsed_content[str(values[0])].append(
            ElementRow(
                element_index=int(values[1]),
                parent_index=(
                    int(values[2]) if values[2] is not None else None
                ),
                subtree_end_index=int(values[3]),
                depth=int(values[4]),
                child_index=int(values[5]),
                tag=str(values[6]),
                namespace_uri=str(values[7]),
                attributes=dict(values[8] or {}),
                text_direct=str(values[9]),
                text_tail=str(values[10]),
            )
        )
    observations: dict[tuple[str, str], LinkObservation] = {}
    for content_sha256, source_url, observed_at in source:
        content_hash = str(content_sha256)
        elements = parsed_content.get(content_hash)
        if elements is None:
            raise RuntimeError(
                "HTML projection dependency is not committed for "
                f"{content_hash}"
            )
        grouped = links_from_elements(elements, page_url=str(source_url))
        for link in (*grouped["internal"], *grouped["external"]):
            key = (str(link["source_url"]), str(link["target_url"]))
            observation = observations.get(key)
            if observation is None:
                observations[key] = LinkObservation(
                    relation_scope=_relation_scope(*key),
                    first_seen_at=observed_at,
                    last_seen_at=observed_at,
                )
            else:
                observation.observe(observed_at)
    rows = [
        _link_row(source_url, target_url, observation)
        for (source_url, target_url), observation in sorted(
            observations.items()
        )
    ]
    _replace(catalogue, "links", rows)


def _refresh_links_incremental(
    catalogue: Catalogue,
    _html_repository: RawHtmlRepository,
    ticks: tuple[DMLTick, ...],
) -> None:
    start_snapshot, end_snapshot = _snapshot_window(ticks)
    observations = _changed_html_observations(
        catalogue,
        start_snapshot=start_snapshot,
        end_snapshot=end_snapshot,
    )
    if not observations:
        logging.info(
            "incremental links snapshots %s-%s html_observations=0",
            start_snapshot,
            end_snapshot,
        )
        return
    hashes = {content_hash for content_hash, _, _ in observations}
    elements_by_hash = _elements_by_hash(catalogue, hashes)
    missing = hashes - elements_by_hash.keys()
    if missing:
        examples = ", ".join(sorted(missing)[:3])
        suffix = ", ..." if len(missing) > 3 else ""
        raise MaterializationDependencyNotReady(
            f"HTML projection dependency is not committed for "
            f"{len(missing)} content hashes ({examples}{suffix})"
        )
    grouped_observations: dict[tuple[str, str], LinkObservation] = {}
    observation_ranges: dict[tuple[str, str], ObservationRange] = {}
    for content_hash, source_url, observed_at in observations:
        key = (content_hash, source_url)
        existing = observation_ranges.get(key)
        if existing is None:
            observation_ranges[key] = ObservationRange(
                first_seen_at=observed_at,
                last_seen_at=observed_at,
            )
        else:
            existing.observe(observed_at)
    for (content_hash, source_url), observed in observation_ranges.items():
        grouped = links_from_elements(
            elements_by_hash[content_hash],
            page_url=source_url,
        )
        for link in (*grouped["internal"], *grouped["external"]):
            pair = (str(link["source_url"]), str(link["target_url"]))
            existing = grouped_observations.get(pair)
            if existing is None:
                grouped_observations[pair] = LinkObservation(
                    relation_scope=_relation_scope(*pair),
                    first_seen_at=observed.first_seen_at,
                    last_seen_at=observed.last_seen_at,
                )
            else:
                existing.observe(observed.first_seen_at)
                existing.observe(observed.last_seen_at)
    rows = [
        _link_row(source_url, target_url, observation)
        for (source_url, target_url), observation in sorted(
            grouped_observations.items()
        )
    ]
    _merge_link_rows(catalogue, rows)
    logging.info(
        "incremental links snapshots %s-%s "
        "html_observations=%s content_hashes=%s link_pairs=%s",
        start_snapshot,
        end_snapshot,
        len(observations),
        len(hashes),
        len(rows),
    )


def _link_row(
    source_url: str,
    target_url: str,
    observation: LinkObservation,
) -> dict[str, object]:
    return {
        "source_page_id": str(page_id_for(source_url)),
        "target_page_id": str(page_id_for(target_url)),
        "source_url": source_url,
        "target_url": target_url,
        "relation_scope": observation.relation_scope,
        "first_seen_at": observation.first_seen_at,
        "last_seen_at": observation.last_seen_at,
    }


def _changed_html_observations(
    catalogue: Catalogue,
    *,
    start_snapshot: int,
    end_snapshot: int,
) -> list[tuple[str, str, datetime]]:
    alias = _sql_string(catalogue.config.alias)
    rows = catalogue.trusted_remote_rows(
        f"""
        WITH changed_documents AS (
          SELECT DISTINCT document_id
          FROM ducklake_table_changes(
            {alias}, 'ingest', 'documents',
            {start_snapshot}, {end_snapshot}
          )
          WHERE lower(detected_media_type) = 'text/html'
            AND change_type IN ('insert', 'update_postimage')
        )
        SELECT documents.content_sha256,
               coalesce(visits.effective_url, visits.requested_url),
               visits.observed_at
        FROM changed_documents
        JOIN ingest.documents AS documents USING (document_id)
        JOIN ingest.visits AS visits USING (visit_id)
        WHERE lower(documents.detected_media_type) = 'text/html'
          AND visits.observed_at IS NOT NULL
        ORDER BY documents.content_sha256, visits.observed_at, visits.visit_id
        """
    )
    return [
        (
            str(content_hash),
            normalize_url(str(source_url)),
            observed_at,
        )
        for content_hash, source_url, observed_at in rows
    ]


def _elements_by_hash(
    catalogue: Catalogue,
    content_hashes: set[str],
) -> dict[str, list[ElementRow]]:
    if not content_hashes:
        return {}
    rows = catalogue.trusted_remote_rows(
        f"""
        SELECT content_sha256, element_index, parent_index,
               subtree_end_index, depth, child_index, tag, namespace,
               attributes, text_direct, text_tail
        FROM material.html_elements
        WHERE content_sha256 IN ({_sql_string_list(content_hashes)})
        ORDER BY content_sha256, element_index
        """
    )
    projected: dict[str, list[ElementRow]] = defaultdict(list)
    for values in rows:
        projected[str(values[0])].append(
            ElementRow(
                element_index=int(values[1]),
                parent_index=(
                    int(values[2]) if values[2] is not None else None
                ),
                subtree_end_index=int(values[3]),
                depth=int(values[4]),
                child_index=int(values[5]),
                tag=str(values[6]),
                namespace_uri=str(values[7]),
                attributes=dict(values[8] or {}),
                text_direct=str(values[9]),
                text_tail=str(values[10]),
            )
        )
    return dict(projected)


def _snapshot_window(ticks: tuple[DMLTick, ...]) -> tuple[int, int]:
    return (
        min(tick.start_snapshot for tick in ticks),
        max(tick.end_snapshot for tick in ticks),
    )


def _changed_material_hashes(
    catalogue: Catalogue,
    *,
    table_name: str,
    start_snapshot: int,
    end_snapshot: int,
) -> set[str]:
    alias = _sql_string(catalogue.config.alias)
    rows = catalogue.trusted_remote_rows(
        f"""
        SELECT DISTINCT content_sha256
        FROM ducklake_table_changes(
          {alias}, 'material', {_sql_string(table_name)},
          {start_snapshot}, {end_snapshot}
        )
        WHERE content_sha256 IS NOT NULL
        """
    )
    return {str(content_hash) for (content_hash,) in rows}


def _replace_content_hash_slices(
    catalogue: Catalogue,
    *,
    table_name: str,
    content_hashes: set[str],
    rows: list[dict[str, object]],
    variant_columns: set[str] = frozenset(),
) -> None:
    if not content_hashes:
        return
    target = f"material.{table_name}"
    registration = f"_atlas_material_{table_name}_delta"
    if rows:
        catalogue.trusted_connection.register(
            registration,
            _arrow_table(rows),
        )
    try:
        with catalogue.remote_transaction():
            values = ", ".join(
                f"({_sql_string(content_hash)})"
                for content_hash in sorted(content_hashes)
            )
            catalogue.trusted_remote_execute(
                f"""
                MERGE INTO {target} AS target
                USING (VALUES {values}) AS changed(content_sha256)
                  ON target.content_sha256 = changed.content_sha256
                WHEN MATCHED THEN DELETE
                """
            )
            if rows:
                names = rows[0].keys()
                projection = ", ".join(
                    f"{name}::JSON::VARIANT AS {name}"
                    if name in variant_columns
                    else name
                    for name in names
                )
                catalogue.trusted_connection.execute(
                    f"INSERT INTO {target} BY NAME "
                    f"SELECT {projection} FROM {registration}"
                )
    finally:
        if rows:
            catalogue.trusted_connection.unregister(registration)


def _merge_page_rows(
    catalogue: Catalogue,
    rows: list[dict[str, object]],
) -> None:
    if not rows:
        return
    with catalogue.remote_transaction():
        for batch in _row_batches(rows):
            values = ", ".join(
                "("
                + ", ".join(
                    (
                        f"UUID {_sql_string(str(row['page_id']))}",
                        _sql_string(str(row["normalized_url"])),
                        _sql_string(str(row["scheme"])),
                        _sql_string(str(row["hostname"])),
                        _sql_nullable_integer(row["port"]),
                        _sql_string(str(row["path"])),
                        _sql_nullable_string(row["query"]),
                        _sql_nullable_string(row["registrable_domain"]),
                    )
                )
                + ")"
                for row in batch
            )
            catalogue.trusted_remote_execute(
                f"""
                MERGE INTO material.pages AS target
                USING (VALUES {values}) AS delta(
                  page_id, normalized_url, scheme, hostname, port, path, query,
                  registrable_domain
                )
                  ON target.page_id = delta.page_id
                 AND target.normalized_url = delta.normalized_url
                WHEN NOT MATCHED THEN INSERT
                """
            )


def _merge_page_observation_rows(
    catalogue: Catalogue,
    rows: list[dict[str, object]],
) -> None:
    if not rows:
        return
    with catalogue.remote_transaction():
        for batch in _row_batches(rows):
            values = ", ".join(
                "("
                + ", ".join(
                    (
                        f"UUID {_sql_string(str(row['page_id']))}",
                        f"UUID {_sql_string(str(row['visit_id']))}",
                        (
                            "NULL"
                            if row["document_id"] is None
                            else f"UUID {_sql_string(str(row['document_id']))}"
                        ),
                        _sql_timestamp(row["observed_at"]),
                    )
                )
                + ")"
                for row in batch
            )
            catalogue.trusted_remote_execute(
                f"""
                MERGE INTO material.page_observations AS target
                USING (VALUES {values}) AS delta(
                  page_id, visit_id, document_id, observed_at
                )
                  ON target.page_id = delta.page_id
                 AND target.visit_id = delta.visit_id
                WHEN NOT MATCHED THEN INSERT
                """
            )


def _merge_link_rows(
    catalogue: Catalogue,
    rows: list[dict[str, object]],
) -> None:
    if not rows:
        return
    with catalogue.remote_transaction():
        for batch in _row_batches(rows):
            values = ", ".join(
                "("
                + ", ".join(
                    (
                        f"UUID {_sql_string(str(row['source_page_id']))}",
                        f"UUID {_sql_string(str(row['target_page_id']))}",
                        _sql_string(str(row["source_url"])),
                        _sql_string(str(row["target_url"])),
                        _sql_string(str(row["relation_scope"])),
                        _sql_timestamp(row["first_seen_at"]),
                        _sql_timestamp(row["last_seen_at"]),
                    )
                )
                + ")"
                for row in batch
            )
            catalogue.trusted_remote_execute(
                f"""
                MERGE INTO material.links AS target
                USING (VALUES {values}) AS delta(
                  source_page_id, target_page_id,
                  source_url, target_url, relation_scope,
                  first_seen_at, last_seen_at
                )
                  ON target.source_page_id = delta.source_page_id
                 AND target.target_page_id = delta.target_page_id
                WHEN MATCHED THEN UPDATE SET
                  relation_scope = delta.relation_scope,
                  first_seen_at = least(
                    target.first_seen_at, delta.first_seen_at
                  ),
                  last_seen_at = greatest(
                    target.last_seen_at, delta.last_seen_at
                  )
                WHEN NOT MATCHED THEN INSERT
                """
            )


def _row_batches(
    rows: list[dict[str, object]],
) -> list[list[dict[str, object]]]:
    return [
        rows[index : index + _MERGE_BATCH]
        for index in range(0, len(rows), _MERGE_BATCH)
    ]


def _replace(
    catalogue: Catalogue,
    table_name: str,
    rows: list[dict[str, object]],
    *,
    map_columns: set[str] = frozenset(),
    variant_columns: set[str] = frozenset(),
) -> None:
    target = f"material.{table_name}"
    with catalogue.remote_transaction():
        catalogue.trusted_remote_execute(f"DELETE FROM {target}")
        if not rows:
            return
        registration = f"_atlas_material_{table_name}"
        catalogue.trusted_connection.register(
            registration, _arrow_table(rows, map_columns=map_columns)
        )
        try:
            if variant_columns:
                names = rows[0].keys()
                projection = ", ".join(
                    f"{name}::JSON::VARIANT AS {name}"
                    if name in variant_columns
                    else name
                    for name in names
                )
                catalogue.trusted_connection.execute(
                    f"INSERT INTO {target} BY NAME "
                    f"SELECT {projection} FROM {registration}"
                )
            else:
                catalogue.trusted_connection.execute(
                    f"INSERT INTO {target} BY NAME SELECT * FROM {registration}"
                )
        finally:
            catalogue.trusted_connection.unregister(registration)


def _arrow_table(
    rows: list[dict[str, object]],
    *,
    map_columns: set[str] = frozenset(),
) -> pa.Table:
    names = rows[0].keys()
    columns: dict[str, pa.Array] = {}
    for name in names:
        values = [row[name] for row in rows]
        if name in map_columns:
            values = [
                list(dict(value or {}).items())
                for value in values
            ]
            columns[name] = pa.array(
                values,
                type=pa.map_(pa.string(), pa.string()),
            )
        else:
            columns[name] = pa.array(values)
    return pa.table(columns)


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sql_string_list(values: set[str]) -> str:
    return ", ".join(_sql_string(value) for value in sorted(values))


def _sql_nullable_string(value: object) -> str:
    return "NULL" if value is None else _sql_string(str(value))


def _sql_nullable_integer(value: object) -> str:
    return "NULL" if value is None else str(int(value))


def _sql_timestamp(value: object) -> str:
    if not isinstance(value, datetime):
        raise TypeError("timestamp SQL values must be datetime instances")
    return f"TIMESTAMPTZ {_sql_string(value.isoformat())}"


def _namespace_name(namespace_uri: str | None) -> str:
    return {
        "http://www.w3.org/1999/xhtml": "HTML",
        "http://www.w3.org/2000/svg": "SVG",
        "http://www.w3.org/1998/Math/MathML": "MathML",
    }.get(namespace_uri, namespace_uri or "HTML")


def _jsonld_type_terms(value: object) -> set[str]:
    terms: set[str] = set()
    if isinstance(value, dict):
        raw_type = value.get("@type")
        if isinstance(raw_type, str):
            terms.add(raw_type)
        elif isinstance(raw_type, list):
            terms.update(item for item in raw_type if isinstance(item, str))
        for child in value.values():
            terms.update(_jsonld_type_terms(child))
    elif isinstance(value, list):
        for child in value:
            terms.update(_jsonld_type_terms(child))
    return terms


def _registrable_domain(hostname: str) -> str | None:
    result = _TLD_EXTRACT(hostname)
    return result.top_domain_under_public_suffix or None


def _relation_scope(source_url: str, target_url: str) -> str:
    source = urlsplit(source_url)
    target = urlsplit(target_url)
    if source_url == target_url:
        return "self"
    source_port = source.port or (80 if source.scheme == "http" else 443)
    target_port = target.port or (80 if target.scheme == "http" else 443)
    if (
        source.scheme,
        source.hostname,
        source_port,
    ) == (
        target.scheme,
        target.hostname,
        target_port,
    ):
        return "same_origin"
    if source.hostname == target.hostname:
        return "same_host"
    source_site = _registrable_domain(source.hostname or "")
    target_site = _registrable_domain(target.hostname or "")
    if source_site is not None and source_site == target_site:
        return "same_site"
    return "external"
