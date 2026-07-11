"""Coordinate Postgres view references with authoritative DuckLake views."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from control.materialized_views.models import MaterializedView
from repository.catalogue.views import CatalogueViewConflictError, CatalogueViewStore, DuckLakeView

from .models import CatalogueViewReference
from .schemas import CatalogueViewRecord


def list_records(session: Session, store: CatalogueViewStore) -> list[CatalogueViewRecord]:
    references = list(
        session.scalars(
            select(CatalogueViewReference).where(CatalogueViewReference.archived_at.is_(None))
        )
    )
    materializations = list(
        session.scalars(
            select(MaterializedView).where(
                MaterializedView.source_view_reference_id.in_(
                    [reference.id for reference in references]
                ),
                MaterializedView.archived_at.is_(None),
            )
        )
    ) if references else []
    dependencies: dict[UUID, list[MaterializedView]] = {}
    for materialization in materializations:
        if materialization.source_view_reference_id is not None:
            dependencies.setdefault(materialization.source_view_reference_id, []).append(materialization)
    by_uuid = {reference.ducklake_view_uuid: reference for reference in references}
    views = store.list()
    records = [
        _record(
            view,
            by_uuid.get(view.view_uuid),
            dependencies.get(by_uuid[view.view_uuid].id, []) if view.view_uuid in by_uuid else [],
        )
        for view in views
    ]
    present = {view.view_uuid for view in views}
    records.extend(
        _missing_record(reference, dependencies.get(reference.id, []))
        for reference in references
        if reference.ducklake_view_uuid not in present
    )
    return sorted(records, key=lambda item: item.qualified_name)


def get_reference(session: Session, reference_id: UUID) -> CatalogueViewReference | None:
    reference = session.get(CatalogueViewReference, reference_id)
    return reference if reference is not None and reference.archived_at is None else None


def get_record(session: Session, store: CatalogueViewStore, reference_id: UUID) -> CatalogueViewRecord | None:
    reference = get_reference(session, reference_id)
    if reference is None:
        return None
    view = store.get(reference.ducklake_view_uuid)
    dependencies = _attached_materializations(session, reference.id)
    return (
        _record(view, reference, dependencies)
        if view is not None
        else _missing_record(reference, dependencies)
    )


def create_reference(session: Session, store: CatalogueViewStore, *, name: str, sql: str, display_name: str | None, description: str | None, created_from_query_revision_id: UUID | None = None) -> CatalogueViewRecord:
    view = store.create(name=name, sql=sql)
    reference = _new_or_revived_reference(
        session, view, display_name=display_name, description=description
    )
    reference.created_from_query_revision_id = created_from_query_revision_id
    try:
        session.flush()
    except IntegrityError as exc:
        raise CatalogueViewConflictError("An Atlas reference for this view already exists.") from exc
    return _record(view, reference)


def adopt_reference(session: Session, store: CatalogueViewStore, *, view_uuid: UUID, display_name: str | None, description: str | None) -> CatalogueViewRecord:
    view = store.get(view_uuid)
    if view is None:
        raise CatalogueViewConflictError("The DuckLake view no longer exists.")
    reference = _new_or_revived_reference(
        session, view, display_name=display_name, description=description
    )
    try:
        session.flush()
    except IntegrityError as exc:
        raise CatalogueViewConflictError("The DuckLake view is already adopted.") from exc
    return _record(view, reference)


def update_reference(session: Session, store: CatalogueViewStore, reference: CatalogueViewReference, *, expected_uuid: UUID, sql: str, display_name: str | None, description: str | None) -> CatalogueViewRecord:
    if reference.ducklake_view_uuid != expected_uuid:
        raise CatalogueViewConflictError("The view reference changed; refresh before editing.")
    view = store.replace(current_uuid=expected_uuid, sql=sql)
    reference.ducklake_view_uuid = view.view_uuid
    reference.schema_name = view.schema_name
    reference.view_name = view.view_name
    if display_name is not None:
        reference.display_name = display_name.strip() or view.view_name
    reference.description = description
    session.flush()
    return _record(view, reference, _attached_materializations(session, reference.id))


def detach_reference(session: Session, reference: CatalogueViewReference) -> None:
    reference.archived_at = datetime.now(UTC)
    session.flush()


def drop_referenced_view(session: Session, store: CatalogueViewStore, reference: CatalogueViewReference, *, expected_uuid: UUID) -> None:
    if reference.ducklake_view_uuid != expected_uuid:
        raise CatalogueViewConflictError("The view reference changed; refresh before dropping.")
    store.drop(current_uuid=expected_uuid)
    detach_reference(session, reference)


def _record(
    view: DuckLakeView,
    reference: CatalogueViewReference | None,
    materializations: list[MaterializedView] | None = None,
) -> CatalogueViewRecord:
    return CatalogueViewRecord(
        id=reference.id if reference else None,
        ducklake_view_uuid=view.view_uuid,
        schema_name=view.schema_name,
        view_name=view.view_name,
        qualified_name=f"{view.schema_name}.{view.view_name}",
        display_name=reference.display_name if reference else view.view_name,
        description=reference.description if reference else None,
        sql=view.sql,
        columns=list(view.columns),
        managed=reference is not None,
        available=True,
        created_at=reference.created_at if reference else None,
        updated_at=reference.updated_at if reference else None,
        created_from_query_revision_id=(reference.created_from_query_revision_id if reference else None),
        attached_materialized_views=_dependency_records(materializations or []),
    )


def _missing_record(
    reference: CatalogueViewReference,
    materializations: list[MaterializedView] | None = None,
) -> CatalogueViewRecord:
    return CatalogueViewRecord(
        id=reference.id,
        ducklake_view_uuid=reference.ducklake_view_uuid,
        schema_name=reference.schema_name,
        view_name=reference.view_name,
        qualified_name=f"{reference.schema_name}.{reference.view_name}",
        display_name=reference.display_name,
        description=reference.description,
        sql="",
        columns=[],
        managed=True,
        available=False,
        created_at=reference.created_at,
        updated_at=reference.updated_at,
        created_from_query_revision_id=reference.created_from_query_revision_id,
        attached_materialized_views=_dependency_records(materializations or []),
    )


def _attached_materializations(
    session: Session, reference_id: UUID
) -> list[MaterializedView]:
    return list(
        session.scalars(
            select(MaterializedView).where(
                MaterializedView.source_view_reference_id == reference_id,
                MaterializedView.archived_at.is_(None),
            )
        )
    )


def _dependency_records(materializations: list[MaterializedView]) -> list[dict[str, object]]:
    return [
        {
            "id": materialization.id,
            "name": materialization.name,
            "display_name": materialization.display_name,
            "refresh_mode": materialization.refresh_mode,
        }
        for materialization in sorted(materializations, key=lambda item: item.name)
    ]


def _new_or_revived_reference(
    session: Session,
    view: DuckLakeView,
    *,
    display_name: str | None,
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
            display_name=(display_name or view.view_name).strip(),
            description=description,
        )
        session.add(reference)
        return reference
    if reference.archived_at is None:
        raise CatalogueViewConflictError("An Atlas reference for this view already exists.")
    reference.ducklake_view_uuid = view.view_uuid
    reference.display_name = (display_name or view.view_name).strip()
    reference.description = description
    reference.archived_at = None
    return reference
