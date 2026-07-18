from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from control.catalogue_views.models import CatalogueViewReference
from repository.catalogue.materializations import (
    MaterializationConflictError,
    MaterializationError,
    MaterializationStore,
    scoped_select,
    scoped_view_query,
)
from repository.catalogue.views import CatalogueViewStore

from .models import CatalogueMaterialization
from .schemas import CatalogueMaterializationRecord, CatalogueMaterializationSummary


def list_records(session: Session) -> list[CatalogueMaterializationRecord]:
    models = session.scalars(
        select(CatalogueMaterialization)
        .where(CatalogueMaterialization.archived_at.is_(None))
        .order_by(CatalogueMaterialization.updated_at.desc())
    )
    return [record(session, model) for model in models]


def get_model(
    session: Session, materialization_id: UUID
) -> CatalogueMaterialization | None:
    model = session.get(CatalogueMaterialization, materialization_id)
    return model if model is not None and model.archived_at is None else None


def summary(
    model: CatalogueMaterialization,
    *,
    definition_is_current: bool,
) -> CatalogueMaterializationSummary:
    return CatalogueMaterializationSummary(
        id=model.id,
        status=_status(model),
        definition_is_current=definition_is_current,
    )


def put_for_view(
    session: Session,
    store: MaterializationStore,
    *,
    view_reference_id: UUID,
    name: str,
    display_name: str | None,
    description: str | None,
    scope_kind: str,
    scope_column: str,
    backfill_scopes_per_minute: int,
    partition_column: str | None,
) -> CatalogueMaterializationRecord:
    reference = session.scalar(
        select(CatalogueViewReference)
        .where(
            CatalogueViewReference.id == view_reference_id,
            CatalogueViewReference.archived_at.is_(None),
        )
        .with_for_update()
    )
    if reference is None:
        raise LookupError("Managed view not found.")
    existing = session.scalar(
        select(CatalogueMaterialization).where(
            CatalogueMaterialization.view_reference_id == reference.id,
            CatalogueMaterialization.archived_at.is_(None),
        )
    )
    if existing is not None:
        return record(session, existing)
    source_view = CatalogueViewStore(store.catalogue).get(reference.ducklake_view_uuid)
    if source_view is None:
        raise LookupError("DuckLake view not found.")
    if scope_kind not in {"document", "crawl"}:
        raise MaterializationError("Materialization requires document or crawl scope.")
    if scope_column not in source_view.columns:
        raise MaterializationError(
            f"Discriminator column {scope_column!r} is not an output of {source_view.qualified_name}."
        )
    activation_snapshot = store.catalogue.latest_snapshot()
    if activation_snapshot is None:
        raise MaterializationError("DuckLake has no activation snapshot.")
    scope_id = _seed_scope(store, scope_kind)
    sql = scoped_view_query(
        store.catalogue,
        source_view,
        scope_kind=scope_kind,
        scope_column=scope_column,
    )
    table = store.create_empty_scoped(
        name=name,
        sql=sql,
        parameters={f"{scope_kind}_id": scope_id},
    )
    if partition_column:
        table = store.set_daily_partition(name=name, column=partition_column)
    wrapper = CatalogueViewStore(store.catalogue).replace(
        current_uuid=source_view.view_uuid,
        sql=_backing_view_sql(store, name),
    )
    reference.ducklake_view_uuid = wrapper.view_uuid
    now = datetime.now(UTC)
    model = CatalogueMaterialization(
        name=name,
        display_name=(display_name or name).strip(),
        description=description,
        source_sql=source_view.sql,
        view_reference_id=reference.id,
        bound_ducklake_view_uuid=wrapper.view_uuid,
        definition_revision_id=uuid4(),
        scope_kind=scope_kind,
        scope_column=scope_column,
        activation_snapshot=activation_snapshot,
        live_enabled=True,
        backfill_enabled=True,
        backfill_scopes_per_minute=backfill_scopes_per_minute,
        partition_column=partition_column,
        ducklake_table_uuid=table.table_uuid,
        last_refreshed_at=now,
    )
    session.add(model)
    session.flush()
    return record(session, model)


def rebuild(
    session: Session,
    store: MaterializationStore,
    model: CatalogueMaterialization,
    *,
    expected_uuid: UUID,
) -> CatalogueMaterializationRecord:
    if model.dematerialization_requested_at is not None:
        raise MaterializationConflictError("This materialization is being removed.")
    reference = session.get(CatalogueViewReference, model.view_reference_id)
    if reference is None:
        raise LookupError("Materialized view source not found.")
    current = store.inspect(model.name)
    if current.table_uuid != expected_uuid:
        raise MaterializationConflictError(
            "The materialized table changed; refresh the page before retrying."
        )
    scope_id = _seed_scope(store, model.scope_kind)
    sql = scoped_select(
        model.source_sql,
        scope_kind=model.scope_kind,
        scope_column=model.scope_column,
    )
    store.validate_scoped_schema(
        name=model.name,
        sql=sql,
        parameters={f"{model.scope_kind}_id": scope_id},
    )
    activation_snapshot = store.catalogue.latest_snapshot()
    if activation_snapshot is None:
        raise MaterializationError("DuckLake has no activation snapshot.")
    model.bound_ducklake_view_uuid = reference.ducklake_view_uuid
    model.source_state = "current"
    model.definition_revision_id = uuid4()
    model.activation_snapshot = activation_snapshot
    model.live_enabled = True
    model.backfill_enabled = True
    session.flush()
    return record(session, model)


def update_maintenance(
    session: Session,
    model: CatalogueMaterialization,
    *,
    live_enabled: bool | None,
    backfill_enabled: bool | None,
    backfill_scopes_per_minute: int | None,
) -> CatalogueMaterializationRecord:
    if model.dematerialization_requested_at is not None:
        raise MaterializationConflictError("This materialization is being removed.")
    if model.source_state != "current":
        raise MaterializationConflictError(
            "Rebuild the materialization against its changed view before resuming updates."
        )
    if live_enabled is not None:
        model.live_enabled = live_enabled
    if backfill_enabled is not None:
        model.backfill_enabled = backfill_enabled
    if backfill_scopes_per_minute is not None:
        model.backfill_scopes_per_minute = backfill_scopes_per_minute
    session.flush()
    return record(session, model)


def request_dematerialization(
    session: Session,
    model: CatalogueMaterialization,
    *,
    expected_uuid: UUID,
) -> CatalogueMaterializationRecord:
    if model.dematerialization_requested_at is not None:
        return record(session, model)
    if model.ducklake_table_uuid != expected_uuid:
        raise MaterializationConflictError(
            "The materialized table changed; refresh before removing it."
        )
    model.live_enabled = False
    model.backfill_enabled = False
    model.dematerialization_requested_at = datetime.now(UTC)
    session.flush()
    return record(session, model)


def record(
    session: Session, model: CatalogueMaterialization
) -> CatalogueMaterializationRecord:
    reference = session.get(CatalogueViewReference, model.view_reference_id)
    return CatalogueMaterializationRecord(
        id=model.id,
        name=model.name,
        qualified_name=f"views.{model.name}",
        display_name=model.display_name,
        description=model.description,
        view_reference_id=model.view_reference_id,
        view_uuid=(
            reference.ducklake_view_uuid
            if reference
            else model.bound_ducklake_view_uuid
        ),
        view_name=(
            f"{reference.schema_name}.{reference.view_name}"
            if reference
            else f"views.{model.name}"
        ),
        scope_kind=model.scope_kind,
        scope_column=model.scope_column,
        activation_snapshot=model.activation_snapshot,
        live_enabled=model.live_enabled,
        backfill_enabled=model.backfill_enabled,
        backfill_scopes_per_minute=model.backfill_scopes_per_minute,
        partition_column=model.partition_column,
        definition_revision_id=model.definition_revision_id,
        status=_status(model),
        source_state=model.source_state,
        dematerialization_requested_at=model.dematerialization_requested_at,
        ducklake_table_uuid=model.ducklake_table_uuid,
        last_refreshed_at=model.last_refreshed_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _status(model: CatalogueMaterialization) -> str:
    if model.dematerialization_requested_at is not None:
        return "dematerializing"
    if model.source_state == "source_changed":
        return "source_changed"
    if model.backfill_enabled:
        return "backfilling"
    if model.live_enabled:
        return "live"
    return "paused"


def _seed_scope(store: MaterializationStore, scope_kind: str) -> str:
    source_table = "documents" if scope_kind == "document" else "crawls"
    identity_column = "document_id" if scope_kind == "document" else "crawl_id"
    table = _qualified(store, store.catalogue.config.schema, source_table)
    row = store.catalogue.connection.execute(
        f"SELECT {identity_column} FROM {table} LIMIT 1"
    ).fetchone()
    return str(row[0]) if row else str(uuid4())


def _qualified(store: MaterializationStore, schema: str, table: str) -> str:
    return ".".join(
        '"' + value.replace('"', '""') + '"'
        for value in (store.catalogue.config.alias, schema, table)
    )


def _backing_view_sql(store: MaterializationStore, name: str) -> str:
    qualified = ".".join(
        '"' + value.replace('"', '""') + '"'
        for value in (store.catalogue.config.alias, "_atlas_materializations", name)
    )
    return f"SELECT * FROM {qualified}"
