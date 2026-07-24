"""Immutable revision history for saved catalogue SQL."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from atlas_sql import AtlasCompiler, CompilationResult
from repository.catalogue.query import classify_select
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
    slug: str,
    description: str | None,
    sql: str,
    change_note: str | None,
    compilation: CompilationResult | None = None,
) -> CatalogueQueryDetail:
    classify_select(sql)
    compilation = compilation or _compile_without_catalogue_snapshot(sql)
    query = CatalogueQuery(slug=slug, description=description)
    session.add(query)
    _flush_slug(session)
    revision = _append_revision(
        query,
        sql=sql,
        change_note=change_note,
        number=1,
        compilation=compilation,
    )
    session.add(revision)
    session.flush()
    query.current_revision_id = revision.id
    session.flush()
    return detail(query)


def list_queries(
    session: Session,
    *,
    archived: bool = False,
) -> list[CatalogueQueryRecord]:
    statement = select(CatalogueQuery).order_by(CatalogueQuery.updated_at.desc())
    statement = statement.where(
        CatalogueQuery.archived_at.is_not(None)
        if archived
        else CatalogueQuery.archived_at.is_(None)
    )
    return [record(query) for query in session.scalars(statement)]


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


def update_query(
    session: Session,
    query: CatalogueQuery,
    *,
    expected_revision_id: UUID,
    sql: str,
    slug: str | None,
    description: str | None,
    change_note: str | None,
    compilation: CompilationResult | None = None,
) -> CatalogueQueryDetail:
    locked = _lock(session, query.id)
    _expect_revision(locked, expected_revision_id)
    classify_select(sql)
    compilation = compilation or _compile_without_catalogue_snapshot(sql)
    current = _current_revision(locked)
    if slug is not None:
        locked.slug = slug
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
            locked,
            sql=sql,
            change_note=change_note,
            number=next_number,
            compilation=compilation,
        )
        session.add(revision)
        session.flush()
        locked.current_revision_id = revision.id
    _flush_slug(session)
    return detail(locked)


def restore_revision(
    session: Session,
    query: CatalogueQuery,
    revision: CatalogueQueryRevision,
    *,
    expected_revision_id: UUID,
    change_note: str | None,
    compilation: CompilationResult | None = None,
) -> CatalogueQueryDetail:
    return update_query(
        session,
        query,
        expected_revision_id=expected_revision_id,
        sql=revision.sql,
        slug=None,
        description=query.description,
        change_note=change_note or f"Restore revision {revision.revision}",
        compilation=compilation,
    )


def archive_query(session: Session, query: CatalogueQuery) -> None:
    query.archived_at = datetime.now(UTC)
    session.flush()


def restore_query(session: Session, query: CatalogueQuery) -> CatalogueQueryDetail:
    query.archived_at = None
    session.flush()
    return detail(query)


def record(query: CatalogueQuery) -> CatalogueQueryRecord:
    current = _current_revision(query)
    return CatalogueQueryRecord(
        id=query.id,
        slug=query.slug,
        description=query.description,
        fixture_path=query.fixture_path,
        current_revision_id=current.id,
        current_revision=current.revision,
        sql=current.sql,
        compiler_outcome=current.compiler_outcome,
        compiler_diagnostics=current.compiler_diagnostics,
        compiler_dependencies=current.compiler_dependencies,
        compiler_version=current.compiler_version,
        catalogue_definition_revision=current.catalogue_definition_revision,
        archived_at=query.archived_at,
        created_at=query.created_at,
        updated_at=query.updated_at,
    )


def detail(query: CatalogueQuery) -> CatalogueQueryDetail:
    return CatalogueQueryDetail(
        **record(query).model_dump(),
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
        compiler_outcome=revision.compiler_outcome,
        compiler_diagnostics=revision.compiler_diagnostics,
        compiler_dependencies=revision.compiler_dependencies,
        compiler_version=revision.compiler_version,
        catalogue_definition_revision=revision.catalogue_definition_revision,
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
    query: CatalogueQuery,
    *,
    sql: str,
    change_note: str | None,
    number: int,
    compilation: CompilationResult,
) -> CatalogueQueryRevision:
    return CatalogueQueryRevision(
        query=query,
        revision=number,
        sql=sql.strip(),
        sql_hash=_hash(sql),
        change_note=change_note,
        compiler_outcome=compilation.outcome.value,
        compiler_diagnostics=[
            item.model_dump(mode="json") for item in compilation.diagnostics
        ],
        compiler_dependencies=[
            item.model_dump(mode="json") for item in compilation.dependencies
        ],
        compiler_version=compilation.compiler_version,
        catalogue_definition_revision=compilation.catalogue_revision,
    )


def _hash(sql: str) -> str:
    return sha256(sql.strip().encode()).hexdigest()


def _compile_without_catalogue_snapshot(sql: str) -> CompilationResult:
    """Compile non-API authoring paths that have no live definition loader."""

    return AtlasCompiler.embedded().compile(
        sql,
        coverage_source="saved_query_authoring",
    )


def _flush_slug(session: Session) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        raise CatalogueQueryConflictError(
            "A saved query with this slug already exists."
        ) from exc
