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
from dom import ElementRow, iter_html_elements, links_from_elements
from repository.catalogue import Catalogue, catalogue_from_env, page_id_for
from repository.objects.config import object_store_from_env
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


WorkloadName = Literal["html_elements", "jsonld_values", "pages", "links"]
_TLD_EXTRACT = tldextract.TLDExtract(suffix_list_urls=())
_ACK_WAIT_SECONDS = 300
_FETCH_BATCH = 100
_FETCH_TIMEOUT_SECONDS = 60


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


def workloads() -> tuple[Workload, ...]:
    return (
        Workload(
            "html_elements",
            "ingest",
            "documents",
            "html_elements",
            _refresh_html_elements,
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

    # A startup refresh is the backfill. It also makes recovery independent of
    # the event stream's retention window.
    await _bootstrap_workload(
        leases,
        workload,
        materialization_lane,
        html_repository,
        stop,
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
    """Backfill once before consuming CDC, retrying dependency ordering."""

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
                "fixed materialization %s startup refresh failed; retrying",
                workload.name,
            )
            await asyncio.sleep(1)


async def _process_tick_batch(
    leases,
    workload: Workload,
    materialization_lane: MaterializationLane,
    html_repository: RawHtmlRepository,
    lane_reporter: CatalogueLaneReporter,
    messages,
) -> None:
    """Coalesce source ticks into one refresh and settle the whole batch."""

    ticks = [DMLTick.model_validate_json(msg.data) for msg in messages]
    lane_reporter.active_operation_count += 1
    try:
        await _refresh_with_lease(
            leases,
            workload,
            materialization_lane,
            html_repository,
            acquire_timeout=0,
        )
    except (OperationLeaseUnavailable, OperationLeaseLost):
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
) -> None:
    async with operation_leases(
        leases,
        (f"material.{workload.target_table}",),
        phase="materialization",
        acquire_timeout=acquire_timeout,
    ):
        await lane.call(workload.refresh, html_repository)


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
) -> list[tuple[str, str]]:
    rows = catalogue.trusted_remote_rows(
        f"""
        SELECT content_sha256, min(object_key)
        FROM ingest.documents
        WHERE lower(detected_media_type) = 'text/html'
        GROUP BY content_sha256
        ORDER BY content_sha256
        """
    )
    return [(str(content_hash), str(object_key)) for content_hash, object_key in rows]


def _refresh_html_elements(
    catalogue: Catalogue, html_repository: RawHtmlRepository
) -> None:
    rows: list[dict[str, object]] = []
    for content_sha256, object_key in _html_documents(catalogue):
        html = html_repository.read(object_key)
        for element in iter_html_elements(html):
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
    _replace(catalogue, "html_elements", rows)


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
    rows: list[dict[str, object]] = []
    for content_sha256, element_index, attributes, text in source:
        attrs = dict(attributes or {})
        media_type = attrs.get("type", "").split(";", 1)[0].strip().lower()
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
    _replace(catalogue, "jsonld_values", rows, variant_columns={"value"})


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
        parsed = urlsplit(normalized)
        explicit_port = parsed.port
        rows_by_url[normalized] = {
            "page_id": str(page_id_for(normalized)),
            "normalized_url": normalized,
            "scheme": parsed.scheme,
            "hostname": parsed.hostname,
            "port": explicit_port,
            "path": parsed.path,
            "query": parsed.query or None,
            "registrable_domain": _registrable_domain(parsed.hostname or ""),
        }
    _replace(catalogue, "pages", list(rows_by_url.values()))


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
        {
            "source_url": source_url,
            "target_url": target_url,
            "relation_scope": observation.relation_scope,
            "first_seen_at": observation.first_seen_at,
            "last_seen_at": observation.last_seen_at,
        }
        for (source_url, target_url), observation in sorted(
            observations.items()
        )
    ]
    _replace(catalogue, "links", rows)


def _replace(
    catalogue: Catalogue,
    table_name: str,
    rows: list[dict[str, object]],
    *,
    variant_columns: set[str] = frozenset(),
) -> None:
    target = f"material.{table_name}"
    with catalogue.remote_transaction():
        catalogue.trusted_remote_execute(f"DELETE FROM {target}")
        if not rows:
            return
        registration = f"_atlas_material_{table_name}"
        catalogue.trusted_connection.register(
            registration, pa.Table.from_pylist(rows)
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
