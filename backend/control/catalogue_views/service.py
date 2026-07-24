"""Coordinate Postgres view references with authoritative DuckLake views."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from control.catalogue_materializations.models import CatalogueMaterialization
from control.catalogue_materializations.schemas import CatalogueMaterializationSummary
from control.catalogue_materializations.service import (
    summary as materialization_summary,
)
from repository.catalogue.query import classify_select, compile_catalogue_definition
from repository.catalogue.definition_compiler import (
    compile_definition_authoring,
    store_compilation,
)
from repository.catalogue.views import (
    CatalogueViewConflictError,
    CatalogueViewStore,
    DuckLakeView,
)

from .models import CatalogueViewReference
from .schemas import CatalogueViewRecord


def list_records(
    session: Session, store: CatalogueViewStore
) -> list[CatalogueViewRecord]:
    references = list(
        session.scalars(
            select(CatalogueViewReference).where(
                CatalogueViewReference.archived_at.is_(None)
            )
        )
    )
    materializations = (
        list(
            session.scalars(
                select(CatalogueMaterialization).where(
                    CatalogueMaterialization.view_reference_id.in_(
                        [reference.id for reference in references]
                    ),
                    CatalogueMaterialization.archived_at.is_(None),
                )
            )
        )
        if references
        else []
    )
    by_reference: dict[UUID, CatalogueMaterialization] = {}
    for materialization in materializations:
        if materialization.view_reference_id is not None:
            by_reference[materialization.view_reference_id] = materialization
    by_uuid = {reference.ducklake_view_uuid: reference for reference in references}
    references_by_id = {reference.id: reference for reference in references}
    views = store.list()
    present = {view.view_uuid for view in views}
    summaries = {
        reference_id: materialization_summary(materialization)
        for reference_id, materialization in by_reference.items()
        if (reference := references_by_id.get(reference_id)) is not None
    }
    records = [
        _record(
            view,
            by_uuid.get(view.view_uuid),
            summaries.get(by_uuid[view.view_uuid].id)
            if view.view_uuid in by_uuid
            else None,
            definition_sql=(
                by_reference[by_uuid[view.view_uuid].id].source_sql
                if view.view_uuid in by_uuid
                and by_uuid[view.view_uuid].id in by_reference
                else None
            ),
        )
        for view in views
    ]
    records.extend(
        _missing_record(reference, summaries.get(reference.id))
        for reference in references
        if reference.ducklake_view_uuid not in present
    )
    return sorted(records, key=lambda item: item.qualified_name)


def get_reference(
    session: Session, reference_id: UUID
) -> CatalogueViewReference | None:
    reference = session.get(CatalogueViewReference, reference_id)
    return (
        reference if reference is not None and reference.archived_at is None else None
    )


def get_record(
    session: Session, store: CatalogueViewStore, reference_id: UUID
) -> CatalogueViewRecord | None:
    reference = get_reference(session, reference_id)
    if reference is None:
        return None
    view = store.get(reference.ducklake_view_uuid)
    materialization = _attached_materialization(session, reference.id)
    summary = (
        materialization_summary(materialization)
        if materialization is not None
        else None
    )
    return (
        _record(
            view,
            reference,
            summary,
            definition_sql=materialization.source_sql if materialization else None,
        )
        if view is not None
        else _missing_record(reference, summary)
    )


def create_reference(
    session: Session,
    store: CatalogueViewStore,
    *,
    slug: str,
    sql: str,
    description: str | None,
    created_from_query_revision_id: UUID | None = None,
) -> CatalogueViewRecord:
    compilation = compile_definition_authoring(
        store.catalogue,
        sql,
        kind="view",
        schema_name="views",
        object_name=slug,
    )
    view = store.create(name=slug, sql=sql)
    reference = _new_or_revived_reference(
        session, view, slug=slug, description=description
    )
    reference.created_from_query_revision_id = created_from_query_revision_id
    store_compilation(reference, compilation)
    _flush_reference(session)
    return _record(view, reference)


def adopt_reference(
    session: Session,
    store: CatalogueViewStore,
    *,
    view_uuid: UUID,
    slug: str,
    description: str | None,
) -> CatalogueViewRecord:
    view = store.get(view_uuid)
    if view is None:
        raise CatalogueViewConflictError("The DuckLake view no longer exists.")
    reference = _new_or_revived_reference(
        session, view, slug=slug, description=description
    )
    _flush_reference(session)
    return _record(view, reference)


def update_reference(
    session: Session,
    store: CatalogueViewStore,
    reference: CatalogueViewReference,
    *,
    expected_uuid: UUID,
    sql: str,
    slug: str,
    description: str | None,
) -> CatalogueViewRecord:
    locked_reference = session.scalar(
        select(CatalogueViewReference)
        .where(
            CatalogueViewReference.id == reference.id,
            CatalogueViewReference.archived_at.is_(None),
        )
        .with_for_update()
    )
    if locked_reference is None:
        raise CatalogueViewConflictError("The Atlas view reference no longer exists.")
    materialization = session.scalar(
        select(CatalogueMaterialization)
        .where(
            CatalogueMaterialization.view_reference_id == locked_reference.id,
            CatalogueMaterialization.archived_at.is_(None),
        )
        .with_for_update()
    )
    if locked_reference.ducklake_view_uuid != expected_uuid:
        raise CatalogueViewConflictError(
            "The view reference changed; refresh before editing."
        )
    classify_select(sql)
    compilation = compile_definition_authoring(
        store.catalogue,
        sql,
        kind="view",
        schema_name=locked_reference.schema_name,
        object_name=locked_reference.view_name,
    )
    if materialization is not None:
        if compile_catalogue_definition(sql) != compile_catalogue_definition(
            materialization.source_sql
        ):
            raise CatalogueViewConflictError(
                "Dematerialize this view before changing its definition."
            )
        locked_reference.slug = slug
        locked_reference.description = description
        store_compilation(locked_reference, compilation)
        _flush_reference(session)
        view = store.get(locked_reference.ducklake_view_uuid)
        if view is None:
            raise CatalogueViewConflictError("The materialized view is missing.")
        return _record_with_materialization(
            session, store, view, locked_reference, materialization
        )
    locked_reference.slug = slug
    locked_reference.description = description
    view = store.replace(current_uuid=expected_uuid, sql=sql)
    _update_reference_identity(locked_reference, view)
    store_compilation(locked_reference, compilation)
    _flush_reference(session)
    return _record(view, locked_reference)


def _update_reference_identity(
    reference: CatalogueViewReference, view: DuckLakeView
) -> None:
    reference.ducklake_view_uuid = view.view_uuid
    reference.schema_name = view.schema_name
    reference.view_name = view.view_name


def _record_with_materialization(
    session: Session,
    store: CatalogueViewStore,
    view: DuckLakeView,
    reference: CatalogueViewReference,
    materialization: CatalogueMaterialization | None,
) -> CatalogueViewRecord:
    summary = (
        materialization_summary(materialization)
        if materialization is not None
        else None
    )
    return _record(
        view,
        reference,
        summary,
        definition_sql=materialization.source_sql if materialization else None,
    )


def detach_reference(session: Session, reference: CatalogueViewReference) -> None:
    _require_no_materialization(session, reference)
    reference.archived_at = datetime.now(UTC)
    session.flush()


def drop_referenced_view(
    session: Session,
    store: CatalogueViewStore,
    reference: CatalogueViewReference,
    *,
    expected_uuid: UUID,
) -> None:
    if reference.ducklake_view_uuid != expected_uuid:
        raise CatalogueViewConflictError(
            "The view reference changed; refresh before dropping."
        )
    _require_no_materialization(session, reference)
    store.drop(current_uuid=expected_uuid)
    reference.archived_at = datetime.now(UTC)
    session.flush()


def _require_no_materialization(
    session: Session, reference: CatalogueViewReference
) -> None:
    if _attached_materialization(session, reference.id) is not None:
        raise CatalogueViewConflictError(
            "Dematerialize this view before detaching or dropping it."
        )


def _record(
    view: DuckLakeView,
    reference: CatalogueViewReference | None,
    materialization: CatalogueMaterializationSummary | None = None,
    definition_sql: str | None = None,
) -> CatalogueViewRecord:
    return CatalogueViewRecord(
        id=reference.id if reference else None,
        ducklake_view_uuid=view.view_uuid,
        schema_name=view.schema_name,
        view_name=view.view_name,
        qualified_name=f"{view.schema_name}.{view.view_name}",
        slug=reference.slug if reference else view.view_name,
        description=reference.description if reference else None,
        fixture_path=reference.fixture_path if reference else None,
        sql=definition_sql or view.sql,
        columns=list(view.columns),
        column_types=list(view.column_types),
        managed=reference is not None,
        available=True,
        created_at=reference.created_at if reference else None,
        updated_at=reference.updated_at if reference else None,
        created_from_query_revision_id=(
            reference.created_from_query_revision_id if reference else None
        ),
        materialization=materialization,
        compiler_outcome=reference.compiler_outcome if reference else None,
        compiler_diagnostics=(
            reference.compiler_diagnostics or [] if reference else []
        ),
        compiler_dependencies=(
            reference.compiler_dependencies or [] if reference else []
        ),
        compiler_version=reference.compiler_version if reference else None,
        catalogue_definition_revision=(
            reference.catalogue_definition_revision if reference else None
        ),
    )


def _missing_record(
    reference: CatalogueViewReference,
    materialization: CatalogueMaterializationSummary | None = None,
) -> CatalogueViewRecord:
    return CatalogueViewRecord(
        id=reference.id,
        ducklake_view_uuid=reference.ducklake_view_uuid,
        schema_name=reference.schema_name,
        view_name=reference.view_name,
        qualified_name=f"{reference.schema_name}.{reference.view_name}",
        slug=reference.slug,
        description=reference.description,
        fixture_path=reference.fixture_path,
        sql="",
        columns=[],
        column_types=[],
        managed=True,
        available=False,
        created_at=reference.created_at,
        updated_at=reference.updated_at,
        created_from_query_revision_id=reference.created_from_query_revision_id,
        materialization=materialization,
        compiler_outcome=reference.compiler_outcome,
        compiler_diagnostics=reference.compiler_diagnostics or [],
        compiler_dependencies=reference.compiler_dependencies or [],
        compiler_version=reference.compiler_version,
        catalogue_definition_revision=reference.catalogue_definition_revision,
    )


def _attached_materialization(
    session: Session, reference_id: UUID
) -> CatalogueMaterialization | None:
    return session.scalar(
        select(CatalogueMaterialization).where(
            CatalogueMaterialization.view_reference_id == reference_id,
            CatalogueMaterialization.archived_at.is_(None),
        )
    )


def _new_or_revived_reference(
    session: Session,
    view: DuckLakeView,
    *,
    slug: str,
    description: str | None,
) -> CatalogueViewReference:
    reference = session.scalar(
        select(CatalogueViewReference).where(
            CatalogueViewReference.schema_name == view.schema_name,
            CatalogueViewReference.view_name == view.view_name,
        )
    )
    if reference is None:
        reference = CatalogueViewReference(
            ducklake_view_uuid=view.view_uuid,
            schema_name=view.schema_name,
            view_name=view.view_name,
            slug=slug,
            description=description,
        )
        session.add(reference)
        return reference
    if reference.archived_at is None:
        raise CatalogueViewConflictError(
            "An Atlas reference for this view already exists."
        )
    reference.ducklake_view_uuid = view.view_uuid
    reference.slug = slug
    reference.description = description
    reference.archived_at = None
    return reference


def _flush_reference(session: Session) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        raise CatalogueViewConflictError(
            "An Atlas view with this slug already exists."
        ) from exc
