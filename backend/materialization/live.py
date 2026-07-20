from __future__ import annotations

import asyncio
import logging

from ducklake_cdc_client import DMLConsumer

from materialization.definitions import active_definitions, scope_job
from materialization.queue import SCOPE_LIVE_SUBJECT
from repository.catalogue import Catalogue, catalogue_from_env
from repository.catalogue.cdc import validate_cdc_extension
from repository.ingestion.health import HealthMonitor
from runtime.catalogue_lane import run_catalogue_operation
from runtime.resource_governor import (
    DURABLE_RESOURCE_WAIT,
    catalogue_request,
    resource_permits,
)


def _close_consumer(
    catalogue: Catalogue, consumer: DMLConsumer, *, drop: bool
) -> None:
    name = consumer.name
    consumer.close(timeout=5.0, cancel=False, release=True)
    if drop:
        catalogue.connection.execute(
            "SELECT * FROM cdc_consumer_drop(?, ?)",
            [catalogue.config.alias, name],
        )


async def _wait(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def run_crawl_planner(
    jetstream,
    stop: asyncio.Event,
    monitor: HealthMonitor | None = None,
    resource_grants=None,
) -> None:
    """Publish deterministic scopes directly from durable crawl changes."""

    while not stop.is_set():
        definitions = await asyncio.to_thread(active_definitions, live=True)
        if not definitions:
            if monitor is not None:
                monitor.subsystem_ready("cdc_crawl_planner")
            await _wait(stop, 1)
            continue
        await _run_active_crawl_planner(
            jetstream,
            stop,
            monitor=monitor,
            resource_grants=resource_grants,
        )


async def _run_active_crawl_planner(
    jetstream,
    stop: asyncio.Event,
    monitor: HealthMonitor | None = None,
    resource_grants=None,
) -> None:
    """Own the CDC consumer only while live definitions need discovery."""

    catalogue = await _run_governed(
        resource_grants, "cdc-open-catalogue", catalogue_from_env
    )
    consumers: dict[str, DMLConsumer] = {}
    try:
        await _run_governed(
            resource_grants,
            "cdc-validate-extension",
            validate_cdc_extension,
            catalogue,
        )
        definitions = await asyncio.to_thread(active_definitions, live=True)
        starts = _required_consumer_starts(definitions)
        if not starts:
            return
        consumers = await _run_governed(
            resource_grants,
            "cdc-open-planner-consumers",
            _open_planner_consumers,
            catalogue,
            starts,
        )
        if monitor is not None:
            monitor.subsystem_ready("cdc_crawl_planner")
        while not stop.is_set():
            definitions = await asyncio.to_thread(active_definitions, live=True)
            if not definitions:
                await _drop_unused_consumers(
                    catalogue,
                    consumers,
                    required=set(),
                    resource_grants=resource_grants,
                )
                return
            required = set(_required_consumer_starts(definitions))
            if required != set(consumers):
                await _drop_unused_consumers(
                    catalogue,
                    consumers,
                    required=required,
                    resource_grants=resource_grants,
                )
                return
            saw_changes = False
            for kind in tuple(consumers):
                consumer = consumers[kind]
                batch = await _run_governed(
                    resource_grants,
                    f"cdc-read-{kind}",
                    consumer.read,
                    max_snapshots=100,
                )
                if batch is None:
                    replacement = await _advance_terminal_consumer(
                        catalogue,
                        consumer,
                        kind=kind,
                        resource_grants=resource_grants,
                    )
                    if replacement is not None:
                        consumers[kind] = replacement
                    continue
                saw_changes = True
                scopes = (
                    _crawl_batch_scopes(definitions, batch)
                    if kind == "crawl"
                    else _url_batch_scopes(definitions, batch)
                )
                for scope in scopes:
                    await jetstream.publish(
                        SCOPE_LIVE_SUBJECT,
                        scope.model_dump_json().encode(),
                        headers={"Nats-Msg-Id": scope.operation_id},
                    )
                await _run_governed(
                    resource_grants, f"cdc-commit-{kind}-position", batch.commit
                )
            if not saw_changes:
                await _wait(stop, 1)
    finally:
        for consumer in consumers.values():
            await _run_governed(
                resource_grants,
                "cdc-release-consumer",
                _close_consumer,
                catalogue,
                consumer,
                drop=False,
            )
        await _run_governed(
            resource_grants, "cdc-close-catalogue", catalogue.close
        )


def _open_crawl_planner_consumer(
    catalogue: Catalogue, start_at: int, on_exists: str
) -> DMLConsumer:
    return DMLConsumer(
        catalogue.lake,
        "atlas-crawl-materialization-planner",
        connection=catalogue.connection,
        table=f"{catalogue.config.schema}.crawls",
        mode="changes",
        start_at=start_at,
        on_exists=on_exists,
        lease_policy="error",
    ).open()


def _open_url_planner_consumer(
    catalogue: Catalogue, start_at: int, on_exists: str
) -> DMLConsumer:
    return DMLConsumer(
        catalogue.lake,
        "atlas-url-materialization-planner",
        connection=catalogue.connection,
        table=f"{catalogue.config.schema}.urls",
        mode="changes",
        start_at=start_at,
        on_exists=on_exists,
        lease_policy="error",
    ).open()


def _open_planner_consumers(
    catalogue: Catalogue,
    starts: dict[str, int],
) -> dict[str, DMLConsumer]:
    consumers: dict[str, DMLConsumer] = {}
    try:
        if "crawl" in starts:
            consumers["crawl"] = _open_crawl_planner_consumer(
                catalogue,
                starts["crawl"],
                "use",
            )
        if "url" in starts:
            consumers["url"] = _open_url_planner_consumer(
                catalogue,
                starts["url"],
                "use",
            )
        return consumers
    except Exception:
        for consumer in consumers.values():
            _close_consumer(catalogue, consumer, drop=False)
        raise


def _required_consumer_starts(definitions) -> dict[str, int]:
    starts: dict[str, int] = {}
    crawl_starts = [
        definition.activation_snapshot
        for definition in definitions
        if definition.scope_kind in {"crawl", "document"}
    ]
    if crawl_starts:
        starts["crawl"] = min(crawl_starts)
    url_starts = [
        definition.activation_snapshot
        for definition in definitions
        if definition.scope_kind == "url"
    ]
    if url_starts:
        starts["url"] = min(url_starts)
    return starts


async def _drop_unused_consumers(
    catalogue: Catalogue,
    consumers: dict[str, DMLConsumer],
    *,
    required: set[str],
    resource_grants,
) -> None:
    for kind in set(consumers) - required:
        consumer = consumers.pop(kind)
        await _run_governed(
            resource_grants,
            f"cdc-drop-{kind}-consumer",
            _close_consumer,
            catalogue,
            consumer,
            drop=True,
        )


async def _advance_terminal_consumer(
    catalogue: Catalogue,
    consumer: DMLConsumer,
    *,
    kind: str,
    resource_grants,
) -> DMLConsumer | None:
    window = await _run_governed(
        resource_grants,
        f"cdc-{kind}-window",
        consumer.window,
        max_snapshots=100,
    )
    if not window.terminal or window.terminal_at_snapshot is None:
        return None
    boundary = window.terminal_at_snapshot
    await _run_governed(
        resource_grants,
        f"cdc-close-{kind}-consumer",
        _close_consumer,
        catalogue,
        consumer,
        drop=True,
    )
    opener = (
        _open_crawl_planner_consumer
        if kind == "crawl"
        else _open_url_planner_consumer
    )
    replacement = await _run_governed(
        resource_grants,
        f"cdc-reopen-{kind}-consumer",
        opener,
        catalogue,
        boundary,
        "error",
    )
    logging.info(
        "advanced %s materialization planner across schema boundary %s",
        kind,
        boundary,
    )
    return replacement


async def _run_blocking(function, *args, **kwargs):
    """Do not close a DuckDB connection until its worker-thread call has returned."""

    return await run_catalogue_operation(function, *args, **kwargs)


async def _run_governed(
    resource_grants, operation_id: str, function, *args, **kwargs
):
    if resource_grants is None:
        return await _run_blocking(function, *args, **kwargs)
    async with resource_permits(
        resource_grants,
        catalogue_request(
            operation_id,
            service_class="live",
            object_read_units=1,
        ),
        acquire_timeout=DURABLE_RESOURCE_WAIT,
    ):
        return await _run_blocking(function, *args, **kwargs)


def _crawl_triggered_scopes(definitions, *, crawl_id: str, document_id):
    return [
        scope_job(
            definition,
            crawl_id if definition.scope_kind == "crawl" else str(document_id),
            "live",
            document_id=str(document_id) if document_id is not None else None,
        )
        for definition in definitions
        if definition.scope_kind == "crawl"
        or (definition.scope_kind == "document" and document_id is not None)
    ]


def _crawl_batch_scopes(definitions, batch):
    crawl_scopes = {
        (str(change.values["crawl_id"]), change.values.get("document_id"))
        for change in batch.changes
        if change.kind.value in {"insert", "update_postimage"}
        and change.values.get("crawl_id")
    }
    return [
        scope
        for crawl_id, document_id in crawl_scopes
        for scope in _crawl_triggered_scopes(
            definitions,
            crawl_id=crawl_id,
            document_id=document_id,
        )
    ]


def _url_batch_scopes(definitions, batch):
    url_ids = {
        str(change.values["url_id"])
        for change in batch.changes
        if change.kind.value in {"insert", "update_postimage"}
        and change.values.get("url_id")
    }
    return [
        scope_job(
            definition,
            url_id,
            "live",
            document_id=None,
        )
        for url_id in url_ids
        for definition in definitions
        if definition.scope_kind == "url"
    ]
