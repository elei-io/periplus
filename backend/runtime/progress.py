from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import UUID

import nats
from nats.errors import TimeoutError as NatsTimeoutError
from nats.js.api import RetentionPolicy, StorageType, StreamConfig
from nats.js.errors import NotFoundError
from config import get_str

from actions.shared.progress import ProgressEvent
from runtime.logging import dependency_recovered, dependency_unavailable, worker_log

_STREAM = "ATLAS_PROGRESS"
_SUBJECTS = ["atlas.task-runs.*.progress"]


def _retention_seconds(value: str | None = None) -> float:
    raw = value or get_str("NATS_RETENTION")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m|h)", raw.strip())
    if match is None:
        raise ValueError("NATS_RETENTION must be a duration such as 30s, 5m, or 1h.")
    amount = float(match.group(1))
    return amount * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[match.group(2)]


def _subject(run_id: UUID) -> str:
    return f"atlas.task-runs.{run_id}.progress"


async def _ignore_connection_error(_exc: Exception) -> None:
    """Suppress nats-py's default traceback logging for best-effort connections."""


async def _connect_nats():
    return await nats.connect(
        get_str("NATS_URL"),
        error_cb=_ignore_connection_error,
        allow_reconnect=False,
        max_reconnect_attempts=1,
        reconnect_time_wait=0.1,
        connect_timeout=1,
    )


async def _jetstream():
    client = await _connect_nats()
    jetstream = client.jetstream()
    config = StreamConfig(
        name=_STREAM,
        subjects=_SUBJECTS,
        retention=RetentionPolicy.LIMITS,
        storage=StorageType.FILE,
        max_age=_retention_seconds(),
    )
    try:
        info = await jetstream.stream_info(_STREAM)
        if info.config.max_age != config.max_age:
            await jetstream.update_stream(config=config)
    except NotFoundError:
        await jetstream.add_stream(config=config)
    return client, jetstream


class ProgressPublisher:
    def __init__(self, run_id: UUID, attempt: int) -> None:
        self.run_id = run_id
        self.attempt = attempt
        self.sequence = 0
        self._client = None
        self._jetstream = None

    async def __aenter__(self) -> ProgressPublisher:
        try:
            self._client, self._jetstream = await _jetstream()
            dependency_recovered("nats")
        except Exception as exc:
            dependency_unavailable("nats", exc)
        return self

    async def __aexit__(self, *_args: object) -> None:
        if self._client is not None:
            try:
                await self._client.drain()
            except Exception as exc:
                dependency_unavailable("nats", exc)

    async def publish(self, event_type: str, data: object) -> None:
        if self._jetstream is None:
            return
        self.sequence += 1
        envelope = {
            "event_id": f"{self.attempt}:{self.sequence}",
            "run_id": str(self.run_id),
            "attempt": self.attempt,
            "sequence": self.sequence,
            "type": event_type,
            "timestamp": datetime.now(UTC).isoformat(),
            "data": data,
        }
        try:
            await self._jetstream.publish(
                _subject(self.run_id),
                json.dumps(envelope, default=str).encode(),
            )
        except Exception as exc:
            worker_log(
                logging.WARNING,
                "nats.progress_dropped",
                run_id=str(self.run_id),
                progress_type=event_type,
                error=str(exc),
            )
            self._jetstream = None

    async def progress(self, event: ProgressEvent) -> None:
        data = {
            key: value
            for key, value in asdict(event).items()
            if value is not None and value != {}
        }
        await self.publish("progress", data)


async def stream_progress(
    run_id: UUID,
    *,
    current_attempt: int,
    after_event_id: tuple[int, int] = (0, 0),
) -> AsyncIterator[dict]:
    client, jetstream = await _jetstream()
    subscription = await jetstream.subscribe(_subject(run_id), ordered_consumer=True)
    try:
        while True:
            try:
                message = await subscription.next_msg(timeout=15)
            except NatsTimeoutError:
                yield {"type": "heartbeat"}
                continue
            envelope = json.loads(message.data)
            await message.ack()
            cursor = (int(envelope.get("attempt", 1)), int(envelope["sequence"]))
            if cursor[0] < current_attempt or cursor <= after_event_id:
                continue
            yield envelope
            if envelope["type"] in {"succeeded", "failed", "cancelled", "skipped"}:
                break
    finally:
        await subscription.unsubscribe()
        await client.drain()


async def nats_available() -> bool:
    client = None
    try:
        client = await _connect_nats()
        return True
    except Exception:
        return False
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass
