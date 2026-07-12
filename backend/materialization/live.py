from __future__ import annotations

import asyncio
import logging

import duckdb
from ducklake_cdc_client import CDCClient, DMLConsumer

from config import get_int, get_str
from control.catalogue_materializations.models import CatalogueMaterialization
from datetime import UTC, datetime

from materialization.definitions import active_definitions, publish_scope, scope_job
from materialization.queue import COMMIT_SUBJECT, CrawlMaterializationFanoutPlanJob
from repository.catalogue import Catalogue, catalogue_from_env
from repository.ingestion.health import HealthMonitor


async def run_live(
    jetstream, stop: asyncio.Event, monitor: HealthMonitor
) -> None:
    consumers: dict[tuple[str, str], tuple[Catalogue, DMLConsumer]] = {}
    try:
        while not stop.is_set():
            definitions = [
                item for item in active_definitions(live=True) if item.scope_kind == "document"
            ]
            maximum = get_int("ATLAS_MATERIALIZATION_MAX_LIVE_DEFINITIONS")
            if len(definitions) > maximum:
                raise RuntimeError(
                    f"{len(definitions)} live materializations exceed the configured "
                    f"limit of {maximum}"
                )
            active_keys = {
                (str(item.id), str(item.definition_revision_id)) for item in definitions
            }
            for key in set(consumers) - active_keys:
                catalogue, consumer = consumers.pop(key)
                _close_consumer(catalogue, consumer, drop=True)
                catalogue.close()
            for definition in definitions:
                key = (str(definition.id), str(definition.definition_revision_id))
                if key not in consumers:
                    try:
                        consumers[key] = await asyncio.to_thread(
                            _open_consumer, definition
                        )
                    except duckdb.Error as exc:
                        if "Resource deadlock avoided" not in str(exc):
                            raise
                        logging.warning(
                            "CDC catalogue lock is still being released; retrying"
                        )
                        await _wait(stop, 2)
                        break
                _, consumer = consumers[key]
                batch = await asyncio.to_thread(
                    consumer.listen,
                    timeout_ms=1000,
                    max_snapshots=100,
                    poll_min_ms=1000,
                )
                if batch is None:
                    window = await asyncio.to_thread(consumer.window, max_snapshots=100)
                    if window.terminal:
                        raise RuntimeError(
                            f"CDC consumer {consumer.name!r} reached a schema boundary "
                            f"at snapshot {window.terminal_at_snapshot}"
                        )
                    continue
                identity_column = (
                    "document_id" if definition.scope_kind == "document" else "crawl_id"
                )
                scope_ids = {
                    str(change.values[identity_column])
                    for change in batch.changes
                    if change.kind.value in {"insert", "update_postimage"}
                    and change.values.get(identity_column)
                }
                for scope_id in scope_ids:
                    await publish_scope(jetstream, definition, scope_id, "live")
                await asyncio.to_thread(batch.commit)
            else:
                monitor.dependencies_ready()
                if not definitions:
                    await _wait(stop, 2)
                continue
    finally:
        for catalogue, consumer in consumers.values():
            _close_consumer(catalogue, consumer, drop=False)
            catalogue.close()


def _open_consumer(definition: CatalogueMaterialization) -> tuple[Catalogue, DMLConsumer]:
    if definition.activation_snapshot is None:
        raise RuntimeError(f"materialization {definition.id} has no activation snapshot")
    catalogue = catalogue_from_env()
    try:
        client = CDCClient(catalogue.lake, install_extension=False)
        actual = client.version()
        expected = get_str("ATLAS_DUCKLAKE_CDC_VERSION")
        if actual != expected:
            raise RuntimeError(
                f"DuckLake CDC version mismatch: expected {expected!r}, got {actual!r}"
            )
        name = f"atlas-materialization-{definition.id.hex}-{definition.definition_revision_id.hex}"
        _drop_obsolete_consumers(catalogue, definition.id.hex, name)
        consumer = DMLConsumer(
            catalogue.lake,
            name,
            table=(
                f"{catalogue.config.schema}.documents"
                if definition.scope_kind == "document"
                else f"{catalogue.config.schema}.crawls"
            ),
            mode="changes",
            start_at=definition.activation_snapshot,
            on_exists="use",
            client=client,
        ).open()
        return catalogue, consumer
    except Exception:
        catalogue.close()
        raise


def _drop_obsolete_consumers(
    catalogue: Catalogue, materialization_hex: str, current_name: str
) -> None:
    prefix = f"atlas-materialization-{materialization_hex}-"
    rows = catalogue.connection.execute(
        "SELECT consumer_name, owner_token FROM cdc_list_consumers(?)",
        [catalogue.config.alias],
    ).fetchall()
    for name, owner_token in rows:
        if str(name).startswith(prefix) and name != current_name and owner_token is None:
            catalogue.connection.execute(
                "SELECT * FROM cdc_consumer_drop(?, ?)",
                [catalogue.config.alias, name],
            )


def _close_consumer(
    catalogue: Catalogue, consumer: DMLConsumer, *, drop: bool
) -> None:
    name = consumer.name
    try:
        consumer.client.cdc_consumer_force_release(name)
    except Exception:
        logging.warning("failed to release CDC consumer %s", name, exc_info=True)
    consumer.close()
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


async def run_crawl_planner(jetstream, stop: asyncio.Event) -> None:
    """Freeze crawl-scoped materialization membership before publishing scope work."""

    catalogue = catalogue_from_env()
    consumer = None
    try:
        client = CDCClient(catalogue.lake, install_extension=False)
        start_at = catalogue.latest_snapshot()
        if start_at is None:
            raise RuntimeError("DuckLake has no snapshot for crawl materialization planning")
        consumer = DMLConsumer(
            catalogue.lake,
            "atlas-crawl-materialization-planner",
            table=f"{catalogue.config.schema}.crawls",
            mode="changes",
            start_at=start_at,
            on_exists="use",
            client=client,
        ).open()
        while not stop.is_set():
            await _reconcile_unplanned_crawls(jetstream, catalogue)
            batch = await asyncio.to_thread(
                consumer.listen,
                timeout_ms=1000,
                max_snapshots=100,
                poll_min_ms=1000,
            )
            if batch is None:
                continue
            crawl_scopes = {
                (str(change.values["crawl_id"]), change.values.get("document_id"))
                for change in batch.changes
                if change.kind.value in {"insert", "update_postimage"}
                and change.values.get("crawl_id")
            }
            definitions = active_definitions(live=True)
            for crawl_id, document_id in crawl_scopes:
                plan = CrawlMaterializationFanoutPlanJob(
                    crawl_id=crawl_id,
                    scopes=_crawl_triggered_scopes(
                        definitions, crawl_id=crawl_id, document_id=document_id
                    ),
                    planned_at=datetime.now(UTC),
                )
                await jetstream.publish(
                    COMMIT_SUBJECT,
                    plan.model_dump_json().encode(),
                    headers={"Nats-Msg-Id": f"crawl-fanout-{crawl_id}"},
                )
            await asyncio.to_thread(batch.commit)
    finally:
        if consumer is not None:
            _close_consumer(catalogue, consumer, drop=False)
        catalogue.close()


async def _reconcile_unplanned_crawls(jetstream, catalogue) -> int:
    table = lambda name: ".".join(
        '"' + part.replace('"', '""') + '"'
        for part in (catalogue.config.alias, catalogue.config.schema, name)
    )
    rows = catalogue.connection.execute(
        f"SELECT c.crawl_id, c.document_id FROM {table('crawls')} AS c LEFT JOIN "
        f"{table('crawl_materialization_fanouts')} AS f USING (crawl_id) "
        "WHERE f.crawl_id IS NULL ORDER BY c.captured_at LIMIT 100"
    ).fetchall()
    definitions = active_definitions(live=True)
    for crawl_id_value, document_id in rows:
        crawl_id = str(crawl_id_value)
        plan = CrawlMaterializationFanoutPlanJob(
            crawl_id=crawl_id,
            scopes=_crawl_triggered_scopes(
                definitions, crawl_id=crawl_id, document_id=document_id
            ),
            planned_at=datetime.now(UTC),
        )
        await jetstream.publish(
            COMMIT_SUBJECT,
            plan.model_dump_json().encode(),
            headers={"Nats-Msg-Id": f"crawl-fanout-{crawl_id}"},
        )
    return len(rows)


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
