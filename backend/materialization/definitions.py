from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal

from sqlalchemy import select

from control.materialized_views.models import MaterializedView
from db.session import session_scope
from materialization.queue import (
    SCOPE_BACKFILL_SUBJECT,
    SCOPE_LIVE_SUBJECT,
    MaterializationScopeJob,
)


def active_definitions(
    *, live: bool = False, backfill: bool = False
) -> list[MaterializedView]:
    with session_scope() as session:
        statement = select(MaterializedView).where(
            MaterializedView.archived_at.is_(None),
            MaterializedView.deletion_requested_at.is_(None),
            MaterializedView.refresh_mode == "scope_incremental",
        )
        if live:
            statement = statement.where(MaterializedView.live_enabled.is_(True))
        if backfill:
            statement = statement.where(MaterializedView.backfill_enabled.is_(True))
        items = list(session.scalars(statement))
        for item in items:
            session.expunge(item)
        return items


async def publish_scope(
    jetstream,
    definition: MaterializedView,
    scope_id: str,
    source: Literal["live", "backfill"],
) -> None:
    operation_id = sha256(
        f"{definition.definition_revision_id}:document:{scope_id}".encode()
    ).hexdigest()
    job = MaterializationScopeJob(
        materialized_view_id=definition.id,
        definition_revision_id=definition.definition_revision_id,
        query_revision_id=definition.query_revision_id,
        target_table=definition.name,
        scope_column=definition.scope_column or "document_id",
        scope_id=scope_id,
        operation_id=operation_id,
        source=source,
        enqueued_at=datetime.now(UTC),
    )
    await jetstream.publish(
        SCOPE_LIVE_SUBJECT if source == "live" else SCOPE_BACKFILL_SUBJECT,
        job.model_dump_json().encode(),
        headers={"Nats-Msg-Id": operation_id},
    )
