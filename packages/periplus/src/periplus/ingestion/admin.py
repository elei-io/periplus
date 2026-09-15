"""Infrastructure-facing repository recovery operations used by the HTTP API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from nats.js.errors import NotFoundError

from periplus.ingestion.queue import (
    DEAD_LETTER_SUBJECT,
    DEAD_LETTER_STREAM,
    DeadLetterEntry,
    decode_dead_letter,
    requeue_dead_letter,
)


class DeadLetterRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int
    entry: DeadLetterEntry


class DeadLetterList(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[DeadLetterRecord]


async def list_dead_letters(jetstream, limit: int) -> DeadLetterList:
    info = await jetstream.stream_info(DEAD_LETTER_STREAM)
    items: list[DeadLetterRecord] = []
    sequence = info.state.last_seq
    scanned = 0
    while sequence >= info.state.first_seq and len(items) < limit and scanned < 500:
        scanned += 1
        try:
            raw = await jetstream.get_msg(DEAD_LETTER_STREAM, seq=sequence)
        except NotFoundError:
            sequence -= 1
            continue
        if raw.subject == DEAD_LETTER_SUBJECT:
            items.append(DeadLetterRecord(sequence=sequence, entry=decode_dead_letter(raw.data)))
        sequence -= 1
    return DeadLetterList(items=items)


async def requeue_repository_dead_letter(jetstream, results, sequence: int) -> DeadLetterRecord:
    entry = await requeue_dead_letter(jetstream, results, sequence)
    return DeadLetterRecord(sequence=sequence, entry=entry)
