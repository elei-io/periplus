"""Bounded reversal of one requested live materialization."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from control.catalogue_materializations.models import CatalogueMaterialization
from control.catalogue_views.models import CatalogueViewReference
from db.session import session_scope
from repository.catalogue.materializations import MaterializationStore
from repository.catalogue.operations import operation_lock, run_with_catalogue_retry
from repository.catalogue.views import CatalogueViewStore


def dematerialize_one(catalogue) -> bool:
    """Archive at most one requested materialization under its final commit fence."""

    changed = False

    def attempt() -> None:
        nonlocal changed
        with operation_lock(catalogue, "materialization-dematerialization"):
            with session_scope() as session:
                model = session.scalar(
                    select(CatalogueMaterialization)
                    .where(
                        CatalogueMaterialization.archived_at.is_(None),
                        CatalogueMaterialization.dematerialization_requested_at.is_not(
                            None
                        ),
                    )
                    .order_by(CatalogueMaterialization.dematerialization_requested_at)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                if model is None:
                    return
                reference = session.get(
                    CatalogueViewReference, model.view_reference_id
                )
                if reference is None:
                    raise RuntimeError(
                        "Materialized view reference is missing during dematerialization"
                    )
                view_store = CatalogueViewStore(catalogue)
                current = view_store.get(reference.ducklake_view_uuid)
                if current is None:
                    raise RuntimeError(
                        "Materialized view is missing during dematerialization"
                    )
                restored = view_store.replace(
                    current_uuid=current.view_uuid, sql=model.source_sql
                )
                reference.ducklake_view_uuid = restored.view_uuid
                present = any(
                    table.table_name == model.name
                    for table in catalogue.lake.table.list(
                        schema_name="_atlas_materializations"
                    )
                )
                if present:
                    MaterializationStore(catalogue).drop_managed(
                        name=model.name,
                        expected_uuid=model.ducklake_table_uuid,
                        materialization_id=model.id,
                    )
                model.archived_at = datetime.now(UTC)
                session.flush()
                changed = True

    run_with_catalogue_retry(
        attempt, description="materialization dematerialization commit"
    )
    return changed
