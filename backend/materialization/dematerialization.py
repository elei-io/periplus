"""Physical reversal of one materialization incarnation."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import duckdb

from control.catalogue_materializations.models import CatalogueMaterialization
from control.catalogue_views.models import CatalogueViewReference
from db.session import session_scope
from repository.catalogue.materializations import (
    MaterializationError,
    MaterializationStore,
    physical_materialization_name,
)
from repository.catalogue.views import CatalogueViewStore
from repository.catalogue.views import CatalogueViewError


def dematerialize_one(catalogue, materialization_id: UUID) -> None:
    with session_scope() as session:
        model = session.get(CatalogueMaterialization, materialization_id)
        if model is None or model.archived_at is not None:
            return
        if model.desired_state != "deleting":
            return
        reference = session.get(CatalogueViewReference, model.view_reference_id)
        if reference is None:
            raise RuntimeError(
                "Materialized view reference is missing during dematerialization."
            )
        view_store = CatalogueViewStore(catalogue)
        current_uuid = (
            reference.ducklake_view_uuid
            if view_store.name_for_uuid(reference.ducklake_view_uuid) is not None
            else None
        )
        if current_uuid is None:
            current = next(
                (
                    view
                    for view in view_store.list()
                    if view.view_name == reference.view_name
                ),
                None,
            )
            if current is None:
                raise RuntimeError(
                    "Materialized view is missing during dematerialization."
                )
            current_uuid = current.view_uuid
        try:
            restored = view_store.replace(
                current_uuid=current_uuid, sql=model.source_sql
            )
            reference.ducklake_view_uuid = restored.view_uuid
        except (CatalogueViewError, duckdb.Error):
            # A dropped or incompatible source can make the original SQL
            # unbindable. Removal must still self-destruct cleanly.
            view_store.drop(current_uuid=current_uuid)
        materializations = MaterializationStore(catalogue)
        physical_name = physical_materialization_name(model.id)
        try:
            target = materializations.table_identity(
                physical_name,
                schema_name="_atlas_materializations",
            )
        except MaterializationError:
            target = None
        if target is not None:
            # Bootstrap may have committed the private table before Atlas
            # recorded its UUID in Postgres. The incarnation-derived private
            # name still resolves its exact identity for cleanup.
            materializations.drop(
                name=physical_name,
                expected_uuid=model.ducklake_table_uuid or target.table_uuid,
            )
        model.archived_at = datetime.now(UTC)
        model.last_error = None
        session.flush()
