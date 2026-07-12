"""Coordinate Postgres view references with authoritative DuckLake views."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from control.catalogue_materializations.models import CatalogueMaterialization
from control.catalogue_materializations.schemas import CatalogueMaterializationSummary
from control.catalogue_materializations.service import summary as materialization_summary
from repository.catalogue.materializations import MaterializationStore
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
            select(CatalogueMaterialization).where(
                CatalogueMaterialization.view_reference_id.in_(
                    [reference.id for reference in references]
                ),
                CatalogueMaterialization.archived_at.is_(None),
            )
        )
    ) if references else []
    by_reference: dict[UUID, CatalogueMaterialization] = {}
    for materialization in materializations:
        if materialization.view_reference_id is not None:
            by_reference[materialization.view_reference_id] = materialization
    by_uuid = {reference.ducklake_view_uuid: reference for reference in references}
    references_by_id = {reference.id: reference for reference in references}
    views = store.list()
    present = {view.view_uuid for view in views}
    materialization_store = MaterializationStore(store.catalogue)
    summaries = {
        reference_id: materialization_summary(
            materialization_store,
            materialization,
            active_query_revision=None,
            definition_is_current=(
                materialization.source_state == "current"
                and reference.ducklake_view_uuid
                == materialization.bound_ducklake_view_uuid
                and reference.ducklake_view_uuid in present
            ),
        )
        for reference_id, materialization in by_reference.items()
        if (reference := references_by_id.get(reference_id)) is not None
    }
    records = [
        _record(
            view,
            by_uuid.get(view.view_uuid),
            summaries.get(by_uuid[view.view_uuid].id) if view.view_uuid in by_uuid else None,
        )
        for view in views
    ]
    records.extend(
        _missing_record(reference, summaries.get(reference.id))
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
    materialization = _attached_materialization(session, reference.id)
    summary = (
        materialization_summary(
            MaterializationStore(store.catalogue),
            materialization,
            active_query_revision=None,
            definition_is_current=(
                materialization.source_state == "current"
                and view is not None
                and reference.ducklake_view_uuid
                == materialization.bound_ducklake_view_uuid
            ),
        )
        if materialization is not None
        else None
    )
    return (
        _record(view, reference, summary)
        if view is not None
        else _missing_record(reference, summary)
    )


def create_reference(session: Session, store: CatalogueViewStore, *, name: str, sql: str, display_name: str | None, description: str | None, created_from_query_revision_id: UUID | None = None, provisioned_by: str = "user") -> CatalogueViewRecord:
    view = store.create(name=name, sql=sql)
    reference = _new_or_revived_reference(
        session, view, display_name=display_name, description=description, provisioned_by=provisioned_by
    )
    reference.created_from_query_revision_id = created_from_query_revision_id
    try:
        session.flush()
    except IntegrityError as exc:
        raise CatalogueViewConflictError("An Atlas reference for this view already exists.") from exc
    return _record(view, reference)


def adopt_reference(session: Session, store: CatalogueViewStore, *, view_uuid: UUID, display_name: str | None, description: str | None, provisioned_by: str = "user") -> CatalogueViewRecord:
    view = store.get(view_uuid)
    if view is None:
        raise CatalogueViewConflictError("The DuckLake view no longer exists.")
    reference = _new_or_revived_reference(
        session, view, display_name=display_name, description=description, provisioned_by=provisioned_by
    )
    try:
        session.flush()
    except IntegrityError as exc:
        raise CatalogueViewConflictError("The DuckLake view is already adopted.") from exc
    return _record(view, reference)


def update_reference(session: Session, store: CatalogueViewStore, reference: CatalogueViewReference, *, expected_uuid: UUID, sql: str, display_name: str | None, description: str | None) -> CatalogueViewRecord:
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
    if materialization is not None and materialization.source_state == "source_changed":
        raise CatalogueViewConflictError(
            "Rebuild or dematerialize the attached materialization before editing this view again."
        )
    if materialization is not None and materialization.source_state == "source_changing":
        current = _view_by_name(store, locked_reference)
        if current is None:
            raise CatalogueViewConflictError(
                "The interrupted source change cannot be reconciled because the DuckLake view is missing."
            )
        if current.view_uuid == locked_reference.ducklake_view_uuid:
            raise CatalogueViewConflictError(
                "A source change is already in progress or was interrupted before DuckLake changed; "
                "recover it before retrying the edit."
            )
        _finish_source_change(
            session, locked_reference, materialization, current
        )
        return _record_with_materialization(session, store, current, locked_reference, materialization)
    if locked_reference.ducklake_view_uuid != expected_uuid:
        raise CatalogueViewConflictError("The view reference changed; refresh before editing.")

    if display_name is not None:
        locked_reference.display_name = display_name.strip() or locked_reference.view_name
    locked_reference.description = description
    if materialization is not None:
        materialization.live_enabled = False
        materialization.backfill_enabled = False
        materialization.source_state = "source_changing"
        materialization.definition_revision_id = uuid4()
        session.flush()
        # This commit is the crash-safety boundary. Producers and queued commits are fenced
        # before the authoritative DuckLake view is replaced.
        session.commit()

    view = store.replace(current_uuid=expected_uuid, sql=sql)
    locked_reference = session.scalar(
        select(CatalogueViewReference)
        .where(CatalogueViewReference.id == reference.id)
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
    if materialization is not None:
        _finish_source_change(session, locked_reference, materialization, view)
    else:
        _update_reference_identity(locked_reference, view)
        session.flush()
    return _record_with_materialization(
        session, store, view, locked_reference, materialization
    )


def recover_source_change(
    session: Session,
    store: CatalogueViewStore,
    reference: CatalogueViewReference,
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
    if materialization is None or materialization.source_state != "source_changing":
        raise CatalogueViewConflictError("This view has no interrupted source change.")
    current = _view_by_name(store, locked_reference)
    if current is None:
        raise CatalogueViewConflictError(
            "The interrupted source change cannot be reconciled because the DuckLake view is missing."
        )
    if current.view_uuid == locked_reference.ducklake_view_uuid:
        materialization.source_state = "current"
        session.flush()
    else:
        _finish_source_change(session, locked_reference, materialization, current)
    return _record_with_materialization(
        session, store, current, locked_reference, materialization
    )


def _finish_source_change(
    session: Session,
    reference: CatalogueViewReference,
    materialization: CatalogueMaterialization,
    view: DuckLakeView,
) -> None:
    _update_reference_identity(reference, view)
    materialization.source_state = "source_changed"
    session.flush()


def _update_reference_identity(
    reference: CatalogueViewReference, view: DuckLakeView
) -> None:
    reference.ducklake_view_uuid = view.view_uuid
    reference.schema_name = view.schema_name
    reference.view_name = view.view_name


def _view_by_name(
    store: CatalogueViewStore, reference: CatalogueViewReference
) -> DuckLakeView | None:
    return next(
        (
            view
            for view in store.list()
            if view.schema_name == reference.schema_name
            and view.view_name == reference.view_name
        ),
        None,
    )


def _record_with_materialization(
    session: Session,
    store: CatalogueViewStore,
    view: DuckLakeView,
    reference: CatalogueViewReference,
    materialization: CatalogueMaterialization | None,
) -> CatalogueViewRecord:
    summary = (
        materialization_summary(
            MaterializationStore(store.catalogue),
            materialization,
            active_query_revision=None,
            definition_is_current=(
                materialization.source_state == "current"
                and view.view_uuid == materialization.bound_ducklake_view_uuid
            ),
        )
        if materialization is not None
        else None
    )
    return _record(view, reference, summary)


def detach_reference(session: Session, reference: CatalogueViewReference) -> None:
    _require_no_materialization(session, reference)
    reference.archived_at = datetime.now(UTC)
    session.flush()


def drop_referenced_view(session: Session, store: CatalogueViewStore, reference: CatalogueViewReference, *, expected_uuid: UUID) -> None:
    if reference.ducklake_view_uuid != expected_uuid:
        raise CatalogueViewConflictError("The view reference changed; refresh before dropping.")
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
        column_types=list(view.column_types),
        managed=reference is not None,
        provisioned_by=reference.provisioned_by if reference else None,
        available=True,
        created_at=reference.created_at if reference else None,
        updated_at=reference.updated_at if reference else None,
        created_from_query_revision_id=(reference.created_from_query_revision_id if reference else None),
        materialization=materialization,
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
        display_name=reference.display_name,
        description=reference.description,
        sql="",
        columns=[],
        column_types=[],
        managed=True,
        provisioned_by=reference.provisioned_by,
        available=False,
        created_at=reference.created_at,
        updated_at=reference.updated_at,
        created_from_query_revision_id=reference.created_from_query_revision_id,
        materialization=materialization,
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
    display_name: str | None,
    description: str | None,
    provisioned_by: str,
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
            provisioned_by=provisioned_by,
        )
        session.add(reference)
        return reference
    if reference.archived_at is None:
        raise CatalogueViewConflictError("An Atlas reference for this view already exists.")
    reference.ducklake_view_uuid = view.view_uuid
    reference.display_name = (display_name or view.view_name).strip()
    reference.description = description
    reference.provisioned_by = provisioned_by
    reference.archived_at = None
    return reference
