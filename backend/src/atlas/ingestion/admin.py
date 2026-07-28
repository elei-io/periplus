"""Infrastructure-facing repository recovery operations used by the HTTP API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from nats.js.errors import NotFoundError

from atlas.ingestion.queue import (
    DEAD_LETTER_SUBJECT,
    DEAD_LETTER_STREAM,
    DeadLetterEntry,
    decode_dead_letter,
    ensure_dead_letter_stream,
    ensure_ingestion_results,
    ensure_repository_stream,
    requeue_dead_letter,
)
from atlas.platform.messaging.client import connect_nats


class DeadLetterRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int
    entry: DeadLetterEntry


class DeadLetterList(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[DeadLetterRecord]


async def list_dead_letters(limit: int) -> DeadLetterList:
    client = await connect_nats()
    try:
        jetstream = client.jetstream()
        await ensure_dead_letter_stream(jetstream)
        info = await jetstream.stream_info(DEAD_LETTER_STREAM)
        items: list[DeadLetterRecord] = []
        sequence = info.state.last_seq
        while sequence >= info.state.first_seq and len(items) < limit:
            try:
                raw = await jetstream.get_msg(DEAD_LETTER_STREAM, seq=sequence)
            except NotFoundError:
                sequence -= 1
                continue
            if raw.subject != DEAD_LETTER_SUBJECT:
                sequence -= 1
                continue
            items.append(
                DeadLetterRecord(
                    sequence=sequence,
                    entry=decode_dead_letter(raw.data),
                )
            )
            sequence -= 1
        return DeadLetterList(items=items)
    finally:
        await client.drain()


async def requeue_repository_dead_letter(sequence: int) -> DeadLetterRecord:
    client = await connect_nats()
    try:
        jetstream = client.jetstream()
        await ensure_repository_stream(jetstream)
        await ensure_dead_letter_stream(jetstream)
        results = await ensure_ingestion_results(jetstream)
        entry = await requeue_dead_letter(jetstream, results, sequence)
        return DeadLetterRecord(sequence=sequence, entry=entry)
    finally:
        await client.drain()
