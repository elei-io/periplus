"""Postgres retirement receipts and bounded, exact-identity corpus write claims.

No Postgres transaction spans object-store or ClickHouse I/O. A claim outlives the fail-stop deadline,
so another worker cannot take over while the previous bounded writer can commit.
"""

from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import logging
import os
import time
from threading import Timer, local
from uuid import uuid4


from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from periplus.platform.postgres.session import session_scope
from periplus.retention.models import WriteClaimRecord

from periplus.platform.execution import (
    WRITE_SECONDS as WRITE_TIMEOUT_SECONDS,
    DRAIN_SECONDS as CLAIM_SECONDS,
)

_owned = local()


class WriteClaimUnavailable(ValueError):
    """Another bounded writer still owns an overlapping identity."""

    def __init__(
        self,
        message: str,
        *,
        blocked_until: Mapping[tuple[str, str], datetime] | None = None,
    ):
        super().__init__(message)
        self.blocked_until = dict(blocked_until or {})


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _insert(session, model):
    return (
        sqlite_insert if session.get_bind().dialect.name == "sqlite" else pg_insert
    )(model)


def _fail_stop() -> None:
    logging.critical(
        "Corpus write exceeded its 300-second ownership bound; terminating process"
    )
    os._exit(70)


def _acquire(keys, owner):
    with session_scope() as session:
        now = _utc(session.scalar(select(func.current_timestamp())))
        expiry = now + timedelta(seconds=CLAIM_SECONDS)
        session.execute(
            _insert(session, WriteClaimRecord)
            .values(
                [
                    dict(kind=k, identity=i, owner=owner, expires_at=expiry)
                    for k, i in keys
                ]
            )
            .on_conflict_do_nothing(index_elements=["kind", "identity"])
        )
        rows = list(
            session.scalars(
                select(WriteClaimRecord)
                .where(
                    tuple_(WriteClaimRecord.kind, WriteClaimRecord.identity).in_(keys)
                )
                .order_by(WriteClaimRecord.kind, WriteClaimRecord.identity)
                .with_for_update()
            )
        )
        if len(rows) != len(keys):
            raise WriteClaimUnavailable("write claim changed during acquisition")
        blocked = {
            (row.kind, row.identity): _utc(row.expires_at)
            for row in rows
            if row.owner != owner and _utc(row.expires_at) > now
        }
        if blocked:
            raise WriteClaimUnavailable(
                "overlapping corpus write is still active", blocked_until=blocked
            )
        for row in rows:
            row.owner, row.expires_at = owner, expiry


@contextmanager
def write_claims(identities: Mapping[str, Iterable[str]], *, wait_seconds: float = 10):
    """Exclude overlapping capture/body writes within the fail-stop deadline.

    Parsing stays outside the claim. A waiting caller owns no claim. Claims
    remain after uncertain writes, outliving both worker and server deadlines.
    Suspending a process beyond its deadline is outside this worker contract.
    """
    keys = sorted(
        {
            (kind, str(identity))
            for kind, values in identities.items()
            for identity in values
        }
    )
    if any(kind not in {"capture", "content"} for kind, _ in keys):
        raise ValueError("unknown corpus write identity kind")
    if getattr(_owned, "keys", None):
        raise RuntimeError("corpus write claims must be acquired together, not nested")
    if not keys:
        yield
        return
    owner = uuid4()
    deadline = time.monotonic() + wait_seconds
    while True:
        started = time.monotonic()
        try:
            _acquire(keys, owner)
            break
        except WriteClaimUnavailable:
            if time.monotonic() >= deadline:
                raise
            time.sleep(min(0.5, max(0, deadline - time.monotonic())))
    remaining = WRITE_TIMEOUT_SECONDS - (time.monotonic() - started)
    if remaining <= 0:
        _fail_stop()
        raise TimeoutError("write claim acquisition exceeded its safe deadline")
    timer = Timer(remaining, _fail_stop)
    timer.daemon = True
    timer.start()
    _owned.keys = frozenset(keys)
    completed = False
    try:
        yield
        completed = True
    except WriteClaimUnavailable:
        # These errors establish rollback or rejection, not an unknown commit.
        completed = True
        raise
    finally:
        try:
            # An uncertain commit may still be executing remotely. Keep its claim
            # until both the worker and server transaction bounds have elapsed.
            if completed:
                with session_scope() as session:
                    session.execute(
                        delete(WriteClaimRecord).where(WriteClaimRecord.owner == owner)
                    )
        except Exception:
            # Work has finished; an unreleased claim safely expires later.
            logging.exception("Could not release completed corpus write claims")
        finally:
            _owned.keys = frozenset()
            timer.cancel()
            timer.join()


def cleanup_expired_claims(limit: int = 1000) -> None:
    with session_scope() as session:
        now = func.current_timestamp()
        keys = (
            select(WriteClaimRecord.kind, WriteClaimRecord.identity)
            .where(WriteClaimRecord.expires_at < now)
            .order_by(WriteClaimRecord.expires_at)
            .limit(limit)
        )
        session.execute(
            delete(WriteClaimRecord).where(
                tuple_(WriteClaimRecord.kind, WriteClaimRecord.identity).in_(keys),
                WriteClaimRecord.expires_at < now,
            )
        )
