"""Resumable raw-object retirement work in control Postgres."""
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import delete, select, tuple_, update

from periplus.platform.postgres.session import session_scope
from periplus.retention.identities import _insert
from periplus.retention.models import RetentionObjectRecord as Object


def enqueue(content_hash, key, size, now):
    with session_scope() as session:
        values = dict(object_key=key, content_sha256=content_hash, stored_bytes=size,
                      retired_at=now, retired_snapshot=-1, snapshots_cleared_at=None,
                      retirement_id=uuid4())
        session.execute(_insert(session, Object).values(**values).on_conflict_do_update(
            index_elements=['object_key'], set_=values))


def candidates(limit, after=None):
    with session_scope() as session:
        query = select(Object.content_sha256, Object.retired_at, Object.object_key)
        if after:
            query = query.where(tuple_(Object.retired_at, Object.object_key) > after)
        return list(session.execute(query.order_by(Object.retired_at, Object.object_key).limit(limit)).all())


def pending_snapshots(hashes, limit):
    with session_scope() as session:
        return list(session.scalars(select(Object.retirement_id).where(
            Object.retired_snapshot == -1, Object.content_sha256.in_(hashes)
        ).order_by(Object.retired_at).limit(limit)))


def anchor_snapshots(episodes, latest):
    if episodes:
        with session_scope() as session:
            session.execute(update(Object).where(Object.retirement_id.in_(episodes),
                Object.retired_snapshot == -1).values(retired_snapshot=latest))


def mark_snapshots_cleared(hashes, oldest, now, limit):
    with session_scope() as session:
        episodes = list(session.scalars(select(Object.retirement_id).where(
            Object.retired_snapshot >= 0, Object.retired_snapshot <= oldest,
            Object.snapshots_cleared_at.is_(None), Object.content_sha256.in_(hashes)
        ).order_by(Object.retired_at).limit(limit)))
        if episodes:
            session.execute(update(Object).where(Object.retirement_id.in_(episodes),
                Object.snapshots_cleared_at.is_(None)).values(snapshots_cleared_at=now))


def reclaimable(hashes, oldest, now, grace_seconds, limit):
    with session_scope() as session:
        return list(session.execute(select(Object.object_key, Object.content_sha256, Object.retirement_id).where(
            Object.retired_snapshot >= 0, Object.retired_snapshot <= oldest,
            Object.snapshots_cleared_at <= now - timedelta(seconds=grace_seconds),
            Object.content_sha256.in_(hashes)
        ).order_by(Object.retired_at, Object.object_key).limit(limit)).all())


def remove(episode):
    with session_scope() as session:
        session.execute(delete(Object).where(Object.retirement_id == episode))
