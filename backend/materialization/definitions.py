from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal

from sqlalchemy import select

from control.catalogue_materializations.models import CatalogueMaterialization
from db.session import session_scope
from materialization.queue import (
    SCOPE_BACKFILL_SUBJECT,
    SCOPE_LIVE_SUBJECT,
    MaterializationScopeJob,
)


def scope_job(
    definition: CatalogueMaterialization,
    scope_id: str,
    source: Literal["live", "backfill"],
    *,
    document_id: str | None,
) -> MaterializationScopeJob:
    operation_id = sha256(
        f"{definition.definition_revision_id}:{definition.scope_kind}:{scope_id}".encode()
    ).hexdigest()
    return MaterializationScopeJob(
        materialization_id=definition.id,
        definition_revision_id=definition.definition_revision_id,
        target_table=definition.name,
        scope_kind=definition.scope_kind,
        scope_column=definition.scope_column,
        scope_id=scope_id,
        document_id=document_id,
        operation_id=operation_id,
        source=source,
        enqueued_at=datetime.now(UTC),
    )


def active_definitions(
    *, live: bool = False, backfill: bool = False
) -> list[CatalogueMaterialization]:
    with session_scope() as session:
        statement = select(CatalogueMaterialization).where(
            CatalogueMaterialization.archived_at.is_(None),
            CatalogueMaterialization.dematerialization_requested_at.is_(None),
            CatalogueMaterialization.source_state == "current",
        )
        if live:
            statement = statement.where(CatalogueMaterialization.live_enabled.is_(True))
        if backfill:
            statement = statement.where(CatalogueMaterialization.backfill_enabled.is_(True))
        items = list(session.scalars(statement))
        for item in items:
            session.expunge(item)
        return items


async def publish_scope(
    jetstream,
    definition: CatalogueMaterialization,
    scope_id: str,
    source: Literal["live", "backfill"],
    *,
    document_id: str | None,
) -> None:
    job = scope_job(
        definition,
        scope_id,
        source,
        document_id=document_id,
    )
    await jetstream.publish(
        SCOPE_LIVE_SUBJECT if source == "live" else SCOPE_BACKFILL_SUBJECT,
        job.model_dump_json().encode(),
        headers={"Nats-Msg-Id": job.operation_id},
    )
