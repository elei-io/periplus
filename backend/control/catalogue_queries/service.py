"""Immutable revision history for saved catalogue SQL."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from repository.catalogue.query import classify_select
from repository.catalogue.materializations import MaterializationStore

from control.catalogue_materializations.models import CatalogueMaterialization
from control.catalogue_materializations.schemas import CatalogueMaterializationSummary
from control.catalogue_materializations.service import summary as materialization_summary

from .models import CatalogueQuery, CatalogueQueryRevision
from .schemas import (
    CatalogueQueryDetail,
    CatalogueQueryRecord,
    CatalogueQueryRevisionRecord,
)


class CatalogueQueryConflictError(ValueError):
    pass


def create_query(
    session: Session,
    *,
    name: str,
    description: str | None,
    sql: str,
    change_note: str | None,
) -> CatalogueQueryDetail:
    classify_select(sql)
    query = CatalogueQuery(name=name.strip(), description=description)
    session.add(query)
    session.flush()
    revision = _append_revision(query, sql=sql, change_note=change_note, number=1)
    session.add(revision)
    session.flush()
    query.current_revision_id = revision.id
    session.flush()
    return detail(query)


def list_queries(
    session: Session,
    store: MaterializationStore,
    *,
    archived: bool = False,
) -> list[CatalogueQueryRecord]:
    statement = select(CatalogueQuery).order_by(CatalogueQuery.updated_at.desc())
    statement = statement.where(
        CatalogueQuery.archived_at.is_not(None)
        if archived
        else CatalogueQuery.archived_at.is_(None)
    )
    queries = list(session.scalars(statement))
    query_ids = [query.id for query in queries]
    materializations = (
        list(
            session.scalars(
                select(CatalogueMaterialization).where(
                    CatalogueMaterialization.query_id.in_(query_ids),
                    CatalogueMaterialization.archived_at.is_(None),
                )
            )
        )
        if query_ids
        else []
    )
    by_query = {
        item.query_id: item for item in materializations if item.query_id is not None
    }
    revision_ids = [
        item.active_query_revision_id
        for item in materializations
        if item.active_query_revision_id is not None
    ]
    revisions = (
        {
            revision.id: revision
            for revision in session.scalars(
                select(CatalogueQueryRevision).where(
                    CatalogueQueryRevision.id.in_(revision_ids)
                )
            )
        }
        if revision_ids
        else {}
    )
    records = []
    for query in queries:
        materialization = by_query.get(query.id)
        summary = None
        if materialization is not None:
            active_revision = revisions.get(materialization.active_query_revision_id)
            summary = materialization_summary(
                store,
                materialization,
                active_query_revision=(
                    active_revision.revision if active_revision is not None else None
                ),
                definition_is_current=(
                    materialization.active_query_revision_id
                    == query.current_revision_id
                ),
            )
        records.append(record(query, materialization=summary))
    return records


def get_query(session: Session, query_id: UUID) -> CatalogueQuery | None:
    return session.get(CatalogueQuery, query_id)


def get_revision(
    session: Session, query_id: UUID, revision_id: UUID
) -> CatalogueQueryRevision | None:
    return session.scalar(
        select(CatalogueQueryRevision).where(
            CatalogueQueryRevision.id == revision_id,
            CatalogueQueryRevision.query_id == query_id,
        )
    )


def get_materialization_summary(
    session: Session,
    store: MaterializationStore,
    query: CatalogueQuery,
) -> CatalogueMaterializationSummary | None:
    materialization = session.scalar(
        select(CatalogueMaterialization).where(
            CatalogueMaterialization.query_id == query.id,
            CatalogueMaterialization.archived_at.is_(None),
        )
    )
    if materialization is None:
        return None
    active_revision = (
        session.get(CatalogueQueryRevision, materialization.active_query_revision_id)
        if materialization.active_query_revision_id is not None
        else None
    )
    return materialization_summary(
        store,
        materialization,
        active_query_revision=(
            active_revision.revision if active_revision is not None else None
        ),
        definition_is_current=(
            materialization.active_query_revision_id == query.current_revision_id
        ),
    )


def update_query(
    session: Session,
    query: CatalogueQuery,
    *,
    expected_revision_id: UUID,
    sql: str,
    name: str | None,
    description: str | None,
    change_note: str | None,
) -> CatalogueQueryDetail:
    locked = _lock(session, query.id)
    _expect_revision(locked, expected_revision_id)
    classify_select(sql)
    current = _current_revision(locked)
    if name is not None:
        locked.name = name.strip()
    locked.description = description
    if _hash(sql) != current.sql_hash:
        next_number = int(
            session.scalar(
                select(func.max(CatalogueQueryRevision.revision)).where(
                    CatalogueQueryRevision.query_id == locked.id
                )
            )
            or 0
        ) + 1
        revision = _append_revision(
            locked, sql=sql, change_note=change_note, number=next_number
        )
        session.add(revision)
        session.flush()
        locked.current_revision_id = revision.id
    session.flush()
    return detail(locked)


def restore_revision(
    session: Session,
    query: CatalogueQuery,
    revision: CatalogueQueryRevision,
    *,
    expected_revision_id: UUID,
    change_note: str | None,
) -> CatalogueQueryDetail:
    return update_query(
        session,
        query,
        expected_revision_id=expected_revision_id,
        sql=revision.sql,
        name=None,
        description=query.description,
        change_note=change_note or f"Restore revision {revision.revision}",
    )


def archive_query(session: Session, query: CatalogueQuery) -> None:
    materialization = session.scalar(
        select(CatalogueMaterialization).where(
            CatalogueMaterialization.query_id == query.id,
            CatalogueMaterialization.archived_at.is_(None),
        )
    )
    if materialization is not None:
        raise CatalogueQueryConflictError(
            "Dematerialize this query before archiving it."
        )
    query.archived_at = datetime.now(UTC)
    session.flush()


def restore_query(session: Session, query: CatalogueQuery) -> CatalogueQueryDetail:
    query.archived_at = None
    session.flush()
    return detail(query)


def record(
    query: CatalogueQuery,
    *,
    materialization: CatalogueMaterializationSummary | None = None,
) -> CatalogueQueryRecord:
    current = _current_revision(query)
    return CatalogueQueryRecord(
        id=query.id,
        name=query.name,
        description=query.description,
        current_revision_id=current.id,
        current_revision=current.revision,
        sql=current.sql,
        archived_at=query.archived_at,
        created_at=query.created_at,
        updated_at=query.updated_at,
        materialization=materialization,
    )


def detail(
    query: CatalogueQuery,
    *,
    materialization: CatalogueMaterializationSummary | None = None,
) -> CatalogueQueryDetail:
    return CatalogueQueryDetail(
        **record(query, materialization=materialization).model_dump(),
        revisions=[revision_record(item) for item in reversed(query.revisions)],
    )


def revision_record(revision: CatalogueQueryRevision) -> CatalogueQueryRevisionRecord:
    return CatalogueQueryRevisionRecord(
        id=revision.id,
        query_id=revision.query_id,
        revision=revision.revision,
        sql=revision.sql,
        sql_hash=revision.sql_hash,
        change_note=revision.change_note,
        created_at=revision.created_at,
    )


def _lock(session: Session, query_id: UUID) -> CatalogueQuery:
    query = session.scalar(
        select(CatalogueQuery)
        .where(CatalogueQuery.id == query_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if query is None:
        raise LookupError("Saved query not found.")
    return query


def _expect_revision(query: CatalogueQuery, expected: UUID) -> None:
    if query.current_revision_id != expected:
        raise CatalogueQueryConflictError(
            "The saved query changed; refresh before saving another revision."
        )


def _current_revision(query: CatalogueQuery) -> CatalogueQueryRevision:
    revision = next(
        (item for item in query.revisions if item.id == query.current_revision_id), None
    )
    if revision is None:
        raise RuntimeError("Saved query has no current revision.")
    return revision


def _append_revision(
    query: CatalogueQuery, *, sql: str, change_note: str | None, number: int
) -> CatalogueQueryRevision:
    return CatalogueQueryRevision(
        query=query,
        revision=number,
        sql=sql.strip(),
        sql_hash=_hash(sql),
        change_note=change_note,
    )


def _hash(sql: str) -> str:
    return sha256(sql.strip().encode()).hexdigest()
