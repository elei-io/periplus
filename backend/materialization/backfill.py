from __future__ import annotations

import asyncio

from sqlalchemy import update

from config import get_int
from control.catalogue_materializations.models import CatalogueMaterialization
from db.session import session_scope
from materialization.definitions import active_definitions, publish_scope
from materialization.queue import (
    COMMIT_DURABLE,
    COMMIT_STREAM,
    SCOPE_BACKFILL_DURABLE,
    SCOPE_STREAM,
)
from repository.catalogue import Catalogue, catalogue_from_env
from runtime.catalogue_lane import run_catalogue_operation


async def run_backfill(jetstream, stop: asyncio.Event) -> None:
    while not stop.is_set():
        definitions = active_definitions(backfill=True)
        if not definitions:
            await _wait(stop, 5)
            continue
        for definition in definitions:
            cursor: str | None = None
            while not stop.is_set():
                scopes = await run_catalogue_operation(
                    _missing_scope_page, definition, cursor
                )
                if not scopes:
                    break
                delay = 60 / max(1, definition.backfill_scopes_per_minute)
                for scope_id in scopes:
                    if stop.is_set():
                        return
                    await publish_scope(jetstream, definition, scope_id, "backfill")
                    cursor = scope_id
                    await _wait(stop, delay)
            await _wait_for_queues(jetstream, stop)
            if await run_catalogue_operation(_backfill_terminal, definition):
                _finish_backfill(definition)
        await _wait(stop, 5)


async def _wait_for_queues(jetstream, stop: asyncio.Event) -> None:
    while not stop.is_set():
        scope = await jetstream.consumer_info(SCOPE_STREAM, SCOPE_BACKFILL_DURABLE)
        commit = await jetstream.consumer_info(COMMIT_STREAM, COMMIT_DURABLE)
        if not any(
            (
                scope.num_pending,
                scope.num_ack_pending,
                commit.num_pending,
                commit.num_ack_pending,
            )
        ):
            return
        await _wait(stop, 1)


def _missing_scope_page(
    definition: CatalogueMaterialization, cursor: str | None
) -> list[str]:
    limit = get_int("ATLAS_MATERIALIZATION_BACKFILL_PAGE_SIZE")
    with catalogue_from_env() as catalogue:
        source_table = "documents" if definition.scope_kind == "document" else "crawls"
        identity = "document_id" if definition.scope_kind == "document" else "crawl_id"
        scopes = _qualified(catalogue, source_table)
        crawls = _qualified(catalogue, "crawls")
        coverage = _qualified(catalogue, "materialization_scope_results")
        eligible = (
            "d.purpose = 'use'"
            if definition.scope_kind == "crawl"
            else f"EXISTS (SELECT 1 FROM {crawls} AS c AT (VERSION => ?) WHERE "
            "c.document_id = d.document_id AND c.purpose = 'use')"
        )
        eligibility_params = (
            []
            if definition.scope_kind == "crawl"
            else [definition.activation_snapshot]
        )
        rows = catalogue.connection.execute(
            f"""
            SELECT d.{identity}
            FROM {scopes} AS d AT (VERSION => ?)
            LEFT JOIN {coverage} AS r
              ON r.definition_revision_id = ?
             AND r.scope_kind = ?
             AND r.scope_id = CAST(d.{identity} AS VARCHAR)
             AND r.status IN ('succeeded', 'failed')
            WHERE {eligible}
              AND r.scope_id IS NULL
              AND (? IS NULL OR CAST(d.{identity} AS VARCHAR) > ?)
            ORDER BY d.{identity}
            LIMIT ?
            """,
            [
                definition.activation_snapshot,
                definition.definition_revision_id,
                definition.scope_kind,
                *eligibility_params,
                cursor,
                cursor,
                limit,
            ],
        ).fetchall()
        return [str(row[0]) for row in rows]


def _backfill_terminal(definition: CatalogueMaterialization) -> bool:
    with catalogue_from_env() as catalogue:
        source_table = "documents" if definition.scope_kind == "document" else "crawls"
        scopes = _qualified(catalogue, source_table)
        crawls = _qualified(catalogue, "crawls")
        coverage = _qualified(catalogue, "materialization_scope_results")
        alias = "c" if definition.scope_kind == "crawl" else "d"
        eligible = (
            "c.purpose = 'use'"
            if definition.scope_kind == "crawl"
            else f"EXISTS (SELECT 1 FROM {crawls} AS c AT (VERSION => ?) WHERE "
            "c.document_id = d.document_id AND c.purpose = 'use')"
        )
        parameters = [definition.activation_snapshot]
        if definition.scope_kind == "document":
            parameters.append(definition.activation_snapshot)
        total = catalogue.connection.execute(
            f"SELECT count(*) FROM {scopes} AS {alias} AT (VERSION => ?) "
            f"WHERE {eligible}",
            parameters,
        ).fetchone()[0]
        completed = catalogue.connection.execute(
            f"SELECT count(*) FROM {coverage} "
            "WHERE definition_revision_id = ? AND scope_kind = ? "
            "AND status IN ('succeeded', 'failed')",
            [definition.definition_revision_id, definition.scope_kind],
        ).fetchone()[0]
        return int(completed) >= int(total)


def _finish_backfill(definition: CatalogueMaterialization) -> None:
    with session_scope() as session:
        session.execute(
            update(CatalogueMaterialization)
            .where(
                CatalogueMaterialization.id == definition.id,
                CatalogueMaterialization.definition_revision_id
                == definition.definition_revision_id,
            )
            .values(backfill_enabled=False)
        )


def _qualified(catalogue: Catalogue, table: str) -> str:
    return ".".join(
        _quote(value)
        for value in (catalogue.config.alias, catalogue.config.schema, table)
    )


def _quote(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


async def _wait(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass
