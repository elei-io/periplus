"""Postgres retirement receipts and bounded, exact-identity lake write claims.

No Postgres transaction spans lake I/O. A claim outlives the fail-stop deadline,
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

import duckdb

from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from periplus.platform.catalogue.exceptions import CatalogueConflictError
from periplus.platform.postgres.session import session_scope
from periplus.retention.models import LakeWriteClaimRecord, RetiredEvidenceRecord

from periplus.platform.config.performance import (
    LAKE_WRITE_TIMEOUT_SECONDS as WRITE_TIMEOUT_SECONDS,
    LAKE_WRITE_CLAIM_SECONDS as CLAIM_SECONDS,
)
_owned = local()


class EvidenceRetired(CatalogueConflictError):
    """A delayed producer cannot resurrect explicitly retired evidence."""


class WriteClaimUnavailable(CatalogueConflictError):
    """Another bounded writer still owns an overlapping identity."""


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _insert(session, model):
    return (sqlite_insert if session.get_bind().dialect.name == 'sqlite' else pg_insert)(model)


def _fail_stop() -> None:
    logging.critical('Lake write exceeded its 300-second ownership bound; terminating process')
    os._exit(70)


def _acquire(keys, owner, allow_retired):
    with session_scope() as session:
        now = _utc(session.scalar(select(func.current_timestamp())))
        expiry = now + timedelta(seconds=CLAIM_SECONDS)
        session.execute(_insert(session, LakeWriteClaimRecord).values([
            dict(kind=k, identity=i, owner=owner, expires_at=expiry) for k, i in keys
        ]).on_conflict_do_nothing(index_elements=['kind', 'identity']))
        rows = list(session.scalars(select(LakeWriteClaimRecord).where(
            tuple_(LakeWriteClaimRecord.kind, LakeWriteClaimRecord.identity).in_(keys)
        ).order_by(LakeWriteClaimRecord.kind, LakeWriteClaimRecord.identity).with_for_update()))
        if len(rows) != len(keys):
            raise WriteClaimUnavailable('write claim changed during acquisition')
        if any(row.owner != owner and _utc(row.expires_at) > now for row in rows):
            raise WriteClaimUnavailable('overlapping lake write is still active')
        if not allow_retired and session.scalar(select(RetiredEvidenceRecord.identity).where(
            tuple_(RetiredEvidenceRecord.kind, RetiredEvidenceRecord.identity).in_(keys)
        ).limit(1)) is not None:
            raise EvidenceRetired('evidence has been retired')
        for row in rows:
            row.owner, row.expires_at = owner, expiry


@contextmanager
def write_claims(identities: Mapping[str, Iterable[str]], *, allow_retired: bool = False,
                 wait_seconds: float = 10):
    """Exclude overlapping writes; generation claims serialize publication commits.

    Preparation belongs outside this scope. The remote write and its Postgres
    completion receipt belong inside. A waiting caller holds no claims or lake
    transaction. Process suspension beyond the hard deadline is outside the
    bounded-worker contract, as with physical object reclamation.
    """
    keys = sorted({(kind, str(identity)) for kind, values in identities.items() for identity in values})
    if any(kind not in {'observation', 'content', 'collection', 'generation'} for kind, _ in keys):
        raise ValueError('unknown lake write identity kind')
    if getattr(_owned, 'keys', None):
        raise RuntimeError('lake write claims must be acquired together, not nested')
    if not keys:
        yield
        return
    owner = uuid4()
    deadline = time.monotonic() + wait_seconds
    while True:
        started = time.monotonic()
        try:
            _acquire(keys, owner, allow_retired)
            break
        except WriteClaimUnavailable:
            if time.monotonic() >= deadline:
                raise
            time.sleep(min(0.5, max(0, deadline - time.monotonic())))
    remaining = WRITE_TIMEOUT_SECONDS - (time.monotonic() - started)
    if remaining <= 0:
        _fail_stop()
        raise TimeoutError('write claim acquisition exceeded its safe deadline')
    timer = Timer(remaining, _fail_stop)
    timer.daemon = True
    timer.start()
    _owned.keys = frozenset(keys)
    completed = False
    try:
        yield
        completed = True
    except (duckdb.TransactionException, CatalogueConflictError):
        # These errors establish rollback or rejection, not an unknown commit.
        completed = True
        raise
    finally:
        try:
            # An uncertain commit may still be executing remotely. Keep its claim
            # until both the worker and server transaction bounds have elapsed.
            if completed:
                with session_scope() as session:
                    session.execute(delete(LakeWriteClaimRecord).where(LakeWriteClaimRecord.owner == owner))
        except Exception:
            # Work has finished; an unreleased claim safely expires later.
            logging.exception('Could not release completed lake write claims')
        finally:
            _owned.keys = frozenset()
            timer.cancel()
            timer.join()


def cleanup_expired_claims(limit: int = 1000) -> None:
    with session_scope() as session:
        now = func.current_timestamp()
        keys = select(LakeWriteClaimRecord.kind, LakeWriteClaimRecord.identity).where(
            LakeWriteClaimRecord.expires_at < now).order_by(LakeWriteClaimRecord.expires_at).limit(limit)
        session.execute(delete(LakeWriteClaimRecord).where(
            tuple_(LakeWriteClaimRecord.kind, LakeWriteClaimRecord.identity).in_(keys),
            LakeWriteClaimRecord.expires_at < now))


def retire(kind: str, identity: str, now: datetime) -> None:
    key = kind, str(identity)
    if key not in getattr(_owned, 'keys', ()):
        raise RuntimeError('retirement requires the exact write claim')
    with session_scope() as session:
        session.execute(_insert(session, RetiredEvidenceRecord).values(
            kind=kind, identity=str(identity), retired_at=now
        ).on_conflict_do_nothing(index_elements=['kind', 'identity']))


def retired_ids(kind: str, identities: Iterable[str]) -> frozenset[str]:
    selected = sorted(set(map(str, identities)))
    if not selected:
        return frozenset()
    with session_scope() as session:
        return frozenset(session.scalars(select(RetiredEvidenceRecord.identity).where(
            RetiredEvidenceRecord.kind == kind, RetiredEvidenceRecord.identity.in_(selected))))


def retired(kind: str, identity: str) -> bool:
    return str(identity) in retired_ids(kind, [identity])
