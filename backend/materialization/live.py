from __future__ import annotations

import asyncio
import logging

from ducklake_cdc_client import CDCClient, DMLConsumer

from config import get_str

from materialization.definitions import active_definitions, scope_job
from materialization.queue import SCOPE_LIVE_SUBJECT
from repository.catalogue import Catalogue, catalogue_from_env
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
    consumer = None
    try:
        await _run_governed(
            resource_grants,
            "cdc-validate-extension",
            _validate_cdc_extension,
            catalogue,
        )
        start_at = await _run_governed(
            resource_grants, "cdc-latest-snapshot", catalogue.latest_snapshot
        )
        if start_at is None:
            raise RuntimeError("DuckLake has no snapshot for crawl materialization planning")
        consumer = await _run_governed(
            resource_grants,
            "cdc-open-consumer",
            _open_crawl_planner_consumer,
            catalogue,
            start_at,
            "use",
        )
        if monitor is not None:
            monitor.subsystem_ready("cdc_crawl_planner")
        while not stop.is_set():
            definitions = await asyncio.to_thread(active_definitions, live=True)
            if not definitions:
                return
            batch = await _run_governed(
                resource_grants,
                "cdc-read",
                consumer.read,
                max_snapshots=100,
            )
            if batch is None:
                window = await _run_governed(
                    resource_grants,
                    "cdc-window",
                    consumer.window,
                    max_snapshots=100,
                )
                if window.terminal and window.terminal_at_snapshot is not None:
                    boundary = window.terminal_at_snapshot
                    await _run_governed(
                        resource_grants,
                        "cdc-close-consumer",
                        _close_consumer, catalogue, consumer, drop=True
                    )
                    consumer = await _run_governed(
                        resource_grants,
                        "cdc-reopen-consumer",
                        _open_crawl_planner_consumer,
                        catalogue,
                        boundary,
                        "error",
                    )
                    logging.info(
                        "advanced crawl materialization planner across schema boundary %s",
                        boundary,
                    )
                else:
                    await _wait(stop, 1)
                continue
            crawl_scopes = {
                (str(change.values["crawl_id"]), change.values.get("document_id"))
                for change in batch.changes
                if change.kind.value in {"insert", "update_postimage"}
                and change.values.get("crawl_id")
                and change.values.get("purpose") == "use"
            }
            for crawl_id, document_id in crawl_scopes:
                for scope in _crawl_triggered_scopes(
                    definitions, crawl_id=crawl_id, document_id=document_id
                ):
                    await jetstream.publish(
                        SCOPE_LIVE_SUBJECT,
                        scope.model_dump_json().encode(),
                        headers={"Nats-Msg-Id": scope.operation_id},
                    )
            await _run_governed(
                resource_grants, "cdc-commit-position", batch.commit
            )
    finally:
        if consumer is not None:
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
        table=f"{catalogue.config.schema}.crawls",
        mode="changes",
        start_at=start_at,
        on_exists=on_exists,
        lease_policy="error",
    ).open()


def _validate_cdc_extension(catalogue: Catalogue) -> None:
    """Load and validate the image-installed community CDC extension."""

    catalogue.connection.execute("LOAD ducklake_cdc")
    client = CDCClient(catalogue.lake, install_extension=False)
    actual = client.version()
    expected = get_str("ATLAS_DUCKLAKE_CDC_VERSION")
    if actual != expected:
        raise RuntimeError(
            f"DuckLake CDC version mismatch: expected {expected!r}, got {actual!r}"
        )


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
        )
        for definition in definitions
        if definition.scope_kind == "crawl"
        or (definition.scope_kind == "document" and document_id is not None)
    ]
