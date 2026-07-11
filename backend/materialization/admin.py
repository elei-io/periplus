from __future__ import annotations

from nats.js.errors import NotFoundError
from pydantic import BaseModel, ConfigDict

from control.materialized_views.models import MaterializedView
from db.session import session_scope
from materialization.queue import (
    DEAD_LETTER_STREAM,
    SCOPE_BACKFILL_SUBJECT,
    SCOPE_LIVE_SUBJECT,
    MaterializationDeadLetter,
    ensure_streams,
)
from repository.ingestion.queue import connect_repository_nats


class MaterializationDeadLetterRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int
    entry: MaterializationDeadLetter


class MaterializationDeadLetterList(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[MaterializationDeadLetterRecord]


async def list_dead_letters(limit: int) -> MaterializationDeadLetterList:
    client = await connect_repository_nats()
    try:
        jetstream = client.jetstream()
        await ensure_streams(jetstream)
        info = await jetstream.stream_info(DEAD_LETTER_STREAM)
        items: list[MaterializationDeadLetterRecord] = []
        if info.state.messages == 0:
            return MaterializationDeadLetterList(items=items)
        sequence = info.state.last_seq
        while sequence >= info.state.first_seq and len(items) < limit:
            try:
                raw = await jetstream.get_msg(DEAD_LETTER_STREAM, seq=sequence)
            except NotFoundError:
                sequence -= 1
                continue
            items.append(
                MaterializationDeadLetterRecord(
                    sequence=sequence,
                    entry=MaterializationDeadLetter.model_validate_json(raw.data),
                )
            )
            sequence -= 1
        return MaterializationDeadLetterList(items=items)
    finally:
        await client.drain()


async def requeue_dead_letter(sequence: int) -> MaterializationDeadLetterRecord:
    client = await connect_repository_nats()
    try:
        jetstream = client.jetstream()
        await ensure_streams(jetstream)
        raw = await jetstream.get_msg(DEAD_LETTER_STREAM, seq=sequence)
        entry = MaterializationDeadLetter.model_validate_json(raw.data)
        with session_scope() as session:
            definition = session.get(MaterializedView, entry.job.materialized_view_id)
            if (
                definition is None
                or definition.archived_at is not None
                or definition.deletion_requested_at is not None
                or definition.definition_revision_id
                != entry.job.definition_revision_id
            ):
                raise RuntimeError(
                    "The dead letter belongs to an inactive materialization revision."
                )
        subject = (
            SCOPE_LIVE_SUBJECT
            if entry.job.source == "live"
            else SCOPE_BACKFILL_SUBJECT
        )
        await jetstream.publish(
            subject,
            entry.job.model_dump_json().encode(),
            headers={
                "Nats-Msg-Id": f"{entry.job.operation_id}-requeue-{sequence}"
            },
        )
        await jetstream.delete_msg(DEAD_LETTER_STREAM, sequence)
        return MaterializationDeadLetterRecord(sequence=sequence, entry=entry)
    finally:
        await client.drain()
