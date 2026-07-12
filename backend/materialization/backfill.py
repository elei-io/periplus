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


async def run_backfill(jetstream, stop: asyncio.Event) -> None:
    while not stop.is_set():
        definitions = active_definitions(backfill=True)
        if not definitions:
            await _wait(stop, 5)
            continue
        for definition in definitions:
            cursor: str | None = None
            while not stop.is_set():
                scopes = await asyncio.to_thread(
                    _missing_document_scope_page, definition, cursor
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
            if await asyncio.to_thread(_backfill_terminal, definition):
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


def _missing_document_scope_page(
    definition: CatalogueMaterialization, cursor: str | None
) -> list[str]:
    limit = get_int("ATLAS_MATERIALIZATION_BACKFILL_PAGE_SIZE")
    with catalogue_from_env() as catalogue:
        documents = _qualified(catalogue, "documents")
        coverage = _qualified(catalogue, "materialization_scope_results")
        rows = catalogue.connection.execute(
            f"""
            SELECT d.document_id
            FROM {documents} AS d AT (VERSION => ?)
            LEFT JOIN {coverage} AS r
              ON r.definition_revision_id = ?
             AND r.scope_kind = 'document'
             AND r.scope_id = d.document_id
             AND r.status IN ('succeeded', 'failed')
            WHERE r.scope_id IS NULL
              AND (? IS NULL OR d.document_id > ?)
            ORDER BY d.document_id
            LIMIT ?
            """,
            [
                definition.activation_snapshot,
                definition.definition_revision_id,
                cursor,
                cursor,
                limit,
            ],
        ).fetchall()
        return [str(row[0]) for row in rows]


def _backfill_terminal(definition: CatalogueMaterialization) -> bool:
    with catalogue_from_env() as catalogue:
        documents = _qualified(catalogue, "documents")
        coverage = _qualified(catalogue, "materialization_scope_results")
        total = catalogue.connection.execute(
            f"SELECT count(*) FROM {documents} AT (VERSION => ?)",
            [definition.activation_snapshot],
        ).fetchone()[0]
        completed = catalogue.connection.execute(
            f"SELECT count(*) FROM {coverage} "
            "WHERE definition_revision_id = ? AND scope_kind = 'document' "
            "AND status IN ('succeeded', 'failed')",
            [definition.definition_revision_id],
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
