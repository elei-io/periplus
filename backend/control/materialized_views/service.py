from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from control.catalogue_queries.models import CatalogueQuery, CatalogueQueryRevision
from control.catalogue_views.models import CatalogueViewReference
from repository.catalogue.materialized_views import (
    MaterializedTable,
    MaterializedViewConflictError,
    MaterializedViewError,
    MaterializedViewStore,
)
from repository.catalogue.views import CatalogueViewStore, DuckLakeView

from .models import MaterializedView
from .schemas import MaterializedViewColumn, MaterializedViewRecord


def list_records(
    session: Session, store: MaterializedViewStore
) -> list[MaterializedViewRecord]:
    models = session.scalars(
        select(MaterializedView)
        .where(MaterializedView.archived_at.is_(None))
        .order_by(MaterializedView.updated_at.desc())
    )
    return [record(session, store, model) for model in models]


def get_model(session: Session, view_id: UUID) -> MaterializedView | None:
    model = session.get(MaterializedView, view_id)
    return model if model is not None and model.archived_at is None else None


def create(
    session: Session,
    store: MaterializedViewStore,
    *,
    name: str,
    display_name: str | None,
    description: str | None,
    query_revision_id: UUID | None,
    source_view_uuid: UUID | None,
    refresh_mode: str,
    scope_kind: str | None,
    scope_column: str | None,
    live_enabled: bool,
    backfill_enabled: bool,
    backfill_scopes_per_minute: int,
    partition_column: str | None,
) -> MaterializedViewRecord:
    revision = (
        session.get(CatalogueQueryRevision, query_revision_id)
        if query_revision_id is not None
        else None
    )
    source_reference = (
        session.scalar(
            select(CatalogueViewReference).where(
                CatalogueViewReference.ducklake_view_uuid == source_view_uuid,
                CatalogueViewReference.archived_at.is_(None),
            )
        )
        if source_view_uuid is not None
        else None
    )
    source_view = (
        CatalogueViewStore(store.catalogue).get(source_reference.ducklake_view_uuid)
        if source_reference is not None
        else None
    )
    if query_revision_id is not None and revision is None:
        raise LookupError("Saved query revision not found.")
    if source_view_uuid is not None and source_reference is None:
        raise LookupError("Adopt this DuckLake view before materializing it.")
    if source_reference is not None and source_view is None:
        raise LookupError("DuckLake view not found.")

    activation_snapshot = None
    if refresh_mode == "scope_incremental":
        if revision is None or scope_kind != "document" or scope_column is None:
            raise MaterializedViewError(
                "Incremental materialization currently requires document scope."
            )
        activation_snapshot = store.catalogue.latest_snapshot()
        if activation_snapshot is None:
            raise MaterializedViewError("DuckLake has no activation snapshot.")
        scope_id = _seed_scope(store, scope_kind)
        table = store.create_empty_scoped(
            name=name,
            sql=revision.sql,
            parameters={f"{scope_kind}_id": scope_id},
        )
    else:
        sql = revision.sql if revision is not None else _view_select(store, source_view)
        table = store.create(name=name, sql=sql)

    if partition_column:
        table = store.set_daily_partition(name=name, column=partition_column)
    now = datetime.now(UTC)
    model = MaterializedView(
        name=name,
        display_name=(display_name or name).strip(),
        description=description,
        query_revision_id=revision.id if revision else None,
        source_view_reference_id=source_reference.id if source_reference else None,
        definition_revision_id=uuid4(),
        refresh_mode=refresh_mode,
        scope_kind=scope_kind,
        scope_column=scope_column,
        activation_snapshot=activation_snapshot,
        live_enabled=live_enabled,
        backfill_enabled=backfill_enabled,
        backfill_scopes_per_minute=backfill_scopes_per_minute,
        partition_column=partition_column,
        ducklake_table_uuid=table.table_uuid,
        last_refreshed_at=now,
    )
    session.add(model)
    session.flush()
    return _record(session, store, model, revision, source_view, table)


def refresh(
    session: Session,
    store: MaterializedViewStore,
    model: MaterializedView,
    *,
    expected_uuid: UUID,
) -> MaterializedViewRecord:
    if model.refresh_mode != "full":
        raise MaterializedViewError(
            "Scoped incremental materialized views are maintained by live and backfill workers."
        )
    revision = (
        session.get(CatalogueQueryRevision, model.query_revision_id)
        if model.query_revision_id
        else None
    )
    source_reference = (
        session.get(CatalogueViewReference, model.source_view_reference_id)
        if model.source_view_reference_id
        else None
    )
    source_view = (
        CatalogueViewStore(store.catalogue).get(source_reference.ducklake_view_uuid)
        if source_reference
        else None
    )
    if revision is None and source_view is None:
        raise LookupError("Materialized-view source not found.")
    sql = revision.sql if revision is not None else _view_select(store, source_view)
    table = store.refresh(name=model.name, expected_uuid=expected_uuid, sql=sql)
    if model.partition_column:
        table = store.set_daily_partition(
            name=model.name, column=model.partition_column
        )
    model.ducklake_table_uuid = table.table_uuid
    model.last_refreshed_at = datetime.now(UTC)
    session.flush()
    return _record(session, store, model, revision, source_view, table)


def update_maintenance(
    session: Session,
    store: MaterializedViewStore,
    model: MaterializedView,
    *,
    live_enabled: bool | None,
    backfill_enabled: bool | None,
    backfill_scopes_per_minute: int | None,
) -> MaterializedViewRecord:
    if model.refresh_mode != "scope_incremental":
        raise MaterializedViewError("Full-refresh tables have no live maintenance.")
    if model.deletion_requested_at is not None:
        raise MaterializedViewConflictError("This materialized view is being deleted.")
    if live_enabled is not None:
        model.live_enabled = live_enabled
    if backfill_enabled is not None:
        model.backfill_enabled = backfill_enabled
    if backfill_scopes_per_minute is not None:
        model.backfill_scopes_per_minute = backfill_scopes_per_minute
    session.flush()
    return record(session, store, model)


def request_deletion(
    session: Session,
    store: MaterializedViewStore,
    model: MaterializedView,
    *,
    expected_uuid: UUID,
) -> MaterializedViewRecord:
    current = store.inspect(model.name)
    if current.table_uuid != expected_uuid:
        raise MaterializedViewConflictError(
            "The materialized table changed; refresh before deleting it."
        )
    model.live_enabled = False
    model.backfill_enabled = False
    model.deletion_requested_at = datetime.now(UTC)
    session.flush()
    return record(session, store, model)


def record(
    session: Session, store: MaterializedViewStore, model: MaterializedView
) -> MaterializedViewRecord:
    revision = (
        session.get(CatalogueQueryRevision, model.query_revision_id)
        if model.query_revision_id
        else None
    )
    source_reference = (
        session.get(CatalogueViewReference, model.source_view_reference_id)
        if model.source_view_reference_id
        else None
    )
    source_view = (
        CatalogueViewStore(store.catalogue).get(source_reference.ducklake_view_uuid)
        if source_reference
        else None
    )
    return _record(session, store, model, revision, source_view, store.inspect(model.name))


def _record(
    session: Session,
    store: MaterializedViewStore,
    model: MaterializedView,
    revision: CatalogueQueryRevision | None,
    source_view: DuckLakeView | None,
    table: MaterializedTable,
) -> MaterializedViewRecord:
    query = (
        session.get(CatalogueQuery, revision.query_id) if revision is not None else None
    )
    completed, failed, last_completed, total = _scope_progress(store, model)
    status = _status(model, completed=completed, failed=failed, total=total)
    return MaterializedViewRecord(
        id=model.id,
        name=model.name,
        qualified_name=f"materialized.{model.name}",
        display_name=model.display_name,
        description=model.description,
        query_revision_id=revision.id if revision else None,
        query_id=query.id if query else None,
        query_name=query.name if query else None,
        query_revision=revision.revision if revision else None,
        source_view_uuid=source_view.view_uuid if source_view else None,
        source_view_name=source_view.qualified_name if source_view else None,
        refresh_mode=model.refresh_mode,
        scope_kind=model.scope_kind,
        activation_snapshot=model.activation_snapshot,
        live_enabled=model.live_enabled,
        backfill_enabled=model.backfill_enabled,
        backfill_scopes_per_minute=model.backfill_scopes_per_minute,
        partition_column=model.partition_column,
        partitioning=list(table.partitioning),
        status=status,
        completed_scopes=completed,
        total_scopes=total,
        failed_scopes=failed,
        last_scope_completed_at=last_completed,
        active_file_count=table.active_file_count,
        active_storage_bytes=table.active_storage_bytes,
        deletion_requested_at=model.deletion_requested_at,
        ducklake_table_uuid=table.table_uuid,
        row_count=table.row_count,
        columns=[
            MaterializedViewColumn(name=name, data_type=data_type, nullable=nullable)
            for name, data_type, nullable in table.columns
        ],
        last_refreshed_at=model.last_refreshed_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _scope_progress(
    store: MaterializedViewStore, model: MaterializedView
) -> tuple[int | None, int, datetime | None, int | None]:
    if model.refresh_mode != "scope_incremental":
        return None, 0, None, None
    coverage = _qualified(store, store.catalogue.config.schema, "materialization_scope_results")
    row = store.catalogue.connection.execute(
        f"""
        SELECT count(*) FILTER (WHERE status = 'succeeded'),
               count(*) FILTER (WHERE status <> 'succeeded'),
               max(completed_at) FILTER (WHERE status = 'succeeded')
        FROM {coverage}
        WHERE definition_revision_id = ?
        """,
        [model.definition_revision_id],
    ).fetchone()
    completed, failed, last_completed = int(row[0]), int(row[1]), row[2]
    total = None
    if model.scope_kind == "document" and model.activation_snapshot is not None:
        total = int(
            store.catalogue.connection.execute(
                f"SELECT count(*) FROM {_qualified(store, store.catalogue.config.schema, 'documents')} "
                "AT (VERSION => ?)",
                [model.activation_snapshot],
            ).fetchone()[0]
        )
    return completed, failed, last_completed, total


def _status(
    model: MaterializedView, *, completed: int | None, failed: int, total: int | None
) -> str:
    if model.deletion_requested_at is not None:
        return "deleting"
    if model.refresh_mode == "full":
        return "full_refresh"
    if failed:
        return "degraded"
    if model.backfill_enabled and total is not None and (completed or 0) < total:
        return "backfilling"
    if model.live_enabled:
        return "live"
    return "paused"


def _seed_scope(store: MaterializedViewStore, scope_kind: str) -> str:
    if scope_kind != "document":
        raise MaterializedViewError("Only document-scoped materialization is supported.")
    table = _qualified(store, store.catalogue.config.schema, "documents")
    row = store.catalogue.connection.execute(
        f"SELECT document_id FROM {table} LIMIT 1"
    ).fetchone()
    return str(row[0]) if row else str(uuid4())


def _view_select(
    store: MaterializedViewStore, source_view: DuckLakeView | None
) -> str:
    if source_view is None:
        raise LookupError("DuckLake view not found.")
    alias = store.catalogue.config.alias.replace('"', '""')
    name = source_view.view_name.replace('"', '""')
    return f'SELECT * FROM "{alias}"."views"."{name}"'


def _qualified(store: MaterializedViewStore, schema: str, table: str) -> str:
    return ".".join(
        '"' + value.replace('"', '""') + '"'
        for value in (store.catalogue.config.alias, schema, table)
    )
