from __future__ import annotations

import asyncio
import logging

import duckdb
from ducklake_cdc_client import DMLConsumer

from config import get_int, get_str
from control.materialized_views.models import MaterializedView
from materialization.definitions import active_definitions, publish_scope
from repository.catalogue.client import Catalogue
from repository.catalogue.config import catalogue_config_from_env
from repository.ingestion.health import HealthMonitor


async def run_live(
    jetstream, stop: asyncio.Event, monitor: HealthMonitor
) -> None:
    consumers: dict[tuple[str, str], tuple[Catalogue, DMLConsumer]] = {}
    try:
        while not stop.is_set():
            definitions = active_definitions(live=True)
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
                document_ids = {
                    str(change.values["document_id"])
                    for change in batch.changes
                    if change.kind.value in {"insert", "update_postimage"}
                    and change.values.get("document_id")
                }
                for document_id in document_ids:
                    await publish_scope(jetstream, definition, document_id, "live")
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


def _open_consumer(definition: MaterializedView) -> tuple[Catalogue, DMLConsumer]:
    if definition.activation_snapshot is None:
        raise RuntimeError(f"materialization {definition.id} has no activation snapshot")
    catalogue = Catalogue(catalogue_config_from_env())
    try:
        catalogue.connection.execute("LOAD ducklake_cdc")
        actual = str(catalogue.connection.execute("SELECT cdc_version()").fetchone()[0])
        expected = get_str("ATLAS_DUCKLAKE_CDC_VERSION")
        if actual != expected:
            raise RuntimeError(
                f"DuckLake CDC version mismatch: expected {expected!r}, got {actual!r}"
            )
        name = f"atlas-mv-{definition.id.hex}-{definition.definition_revision_id.hex}"
        _drop_obsolete_consumers(catalogue, definition.id.hex, name)
        consumer = DMLConsumer(
            catalogue.lake,
            name,
            table=f"{catalogue.config.schema}.documents",
            mode="changes",
            start_at=definition.activation_snapshot,
            on_exists="use",
        ).open()
        return catalogue, consumer
    except Exception:
        catalogue.close()
        raise


def _drop_obsolete_consumers(
    catalogue: Catalogue, materialized_view_hex: str, current_name: str
) -> None:
    prefix = f"atlas-mv-{materialized_view_hex}-"
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
