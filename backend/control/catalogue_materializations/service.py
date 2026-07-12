from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from control.catalogue_queries.models import CatalogueQuery, CatalogueQueryRevision
from control.catalogue_views.models import CatalogueViewReference
from repository.catalogue.materializations import (
    MaterializationConflictError,
    MaterializationError,
    MaterializationStore,
    MaterializationTable,
    scoped_view_query,
)
from repository.catalogue.views import CatalogueViewStore, DuckLakeView

from .models import CatalogueMaterialization
from .schemas import (
    CatalogueMaterializationColumn,
    CatalogueMaterializationRecord,
    CatalogueMaterializationSummary,
)


def list_records(
    session: Session, store: MaterializationStore
) -> list[CatalogueMaterializationRecord]:
    models = session.scalars(
        select(CatalogueMaterialization)
        .where(CatalogueMaterialization.archived_at.is_(None))
        .order_by(CatalogueMaterialization.updated_at.desc())
    )
    return [record(session, store, model) for model in models]


def get_model(
    session: Session, materialization_id: UUID
) -> CatalogueMaterialization | None:
    model = session.get(CatalogueMaterialization, materialization_id)
    return model if model is not None and model.archived_at is None else None


def summary(
    store: MaterializationStore,
    model: CatalogueMaterialization,
    *,
    active_query_revision: int | None,
    definition_is_current: bool,
) -> CatalogueMaterializationSummary:
    try:
        table = store.inspect(model.name)
        completed, failed, _, total = _scope_progress(store, model)
        status = _status(model, completed=completed, failed=failed, total=total)
        row_count = table.row_count
        storage_bytes = table.active_storage_bytes
    except MaterializationError:
        status = "degraded"
        row_count = 0
        storage_bytes = 0
    return CatalogueMaterializationSummary(
        id=model.id,
        status=status,
        refresh_mode=model.refresh_mode,
        row_count=row_count,
        storage_bytes=storage_bytes,
        active_query_revision=active_query_revision,
        definition_is_current=definition_is_current,
    )


def put_for_query(
    session: Session,
    store: MaterializationStore,
    *,
    query_id: UUID,
    active_query_revision_id: UUID | None,
    name: str,
    display_name: str | None,
    description: str | None,
    refresh_mode: str,
    scope_kind: str | None,
    scope_column: str | None,
    live_enabled: bool,
    backfill_enabled: bool,
    backfill_scopes_per_minute: int,
    partition_column: str | None,
) -> CatalogueMaterializationRecord:
    query = session.scalar(
        select(CatalogueQuery)
        .where(CatalogueQuery.id == query_id, CatalogueQuery.archived_at.is_(None))
        .with_for_update()
    )
    if query is None:
        raise LookupError("Saved query not found.")
    existing = session.scalar(
        select(CatalogueMaterialization).where(
            CatalogueMaterialization.query_id == query.id,
            CatalogueMaterialization.archived_at.is_(None),
        )
    )
    if existing is not None:
        return record(session, store, existing)
    revision_id = active_query_revision_id or query.current_revision_id
    revision = session.get(CatalogueQueryRevision, revision_id) if revision_id else None
    if revision is None or revision.query_id != query.id:
        raise LookupError("Saved query revision not found for this query.")
    return _create(
        session,
        store,
        query=query,
        revision=revision,
        source_reference=None,
        source_view=None,
        name=name,
        display_name=display_name,
        description=description,
        refresh_mode=refresh_mode,
        scope_kind=scope_kind,
        scope_column=scope_column,
        live_enabled=live_enabled,
        backfill_enabled=backfill_enabled,
        backfill_scopes_per_minute=backfill_scopes_per_minute,
        partition_column=partition_column,
    )


def put_for_view(
    session: Session,
    store: MaterializationStore,
    *,
    view_reference_id: UUID,
    name: str,
    display_name: str | None,
    description: str | None,
    refresh_mode: str,
    scope_kind: str | None,
    scope_column: str | None,
    live_enabled: bool,
    backfill_enabled: bool,
    backfill_scopes_per_minute: int,
    partition_column: str | None,
) -> CatalogueMaterializationRecord:
    source_reference = session.scalar(
        select(CatalogueViewReference)
        .where(
            CatalogueViewReference.id == view_reference_id,
            CatalogueViewReference.archived_at.is_(None),
        )
        .with_for_update()
    )
    if source_reference is None:
        raise LookupError("Managed view not found.")
    existing = session.scalar(
        select(CatalogueMaterialization).where(
            CatalogueMaterialization.view_reference_id == source_reference.id,
            CatalogueMaterialization.archived_at.is_(None),
        )
    )
    if existing is not None:
        return record(session, store, existing)
    source_view = CatalogueViewStore(store.catalogue).get(
        source_reference.ducklake_view_uuid
    )
    if source_view is None:
        raise LookupError("DuckLake view not found.")
    return _create(
        session,
        store,
        query=None,
        revision=None,
        source_reference=source_reference,
        source_view=source_view,
        name=name,
        display_name=display_name,
        description=description,
        refresh_mode=refresh_mode,
        scope_kind=scope_kind,
        scope_column=scope_column,
        live_enabled=live_enabled,
        backfill_enabled=backfill_enabled,
        backfill_scopes_per_minute=backfill_scopes_per_minute,
        partition_column=partition_column,
    )


def _create(
    session: Session,
    store: MaterializationStore,
    *,
    query: CatalogueQuery | None,
    revision: CatalogueQueryRevision | None,
    source_reference: CatalogueViewReference | None,
    source_view: DuckLakeView | None,
    name: str,
    display_name: str | None,
    description: str | None,
    refresh_mode: str,
    scope_kind: str | None,
    scope_column: str | None,
    live_enabled: bool,
    backfill_enabled: bool,
    backfill_scopes_per_minute: int,
    partition_column: str | None,
) -> CatalogueMaterializationRecord:

    activation_snapshot = None
    if refresh_mode == "scope_incremental":
        if scope_kind != "document" or scope_column is None:
            raise MaterializationError(
                "Incremental materialization currently requires document scope."
            )
        activation_snapshot = store.catalogue.latest_snapshot()
        if activation_snapshot is None:
            raise MaterializationError("DuckLake has no activation snapshot.")
        scope_id = _seed_scope(store, scope_kind)
        sql = (
            revision.sql
            if revision is not None
            else scoped_view_query(
                store.catalogue, _require_view(source_view), scope_column=scope_column
            )
        )
        table = store.create_empty_scoped(
            name=name,
            sql=sql,
            parameters={f"{scope_kind}_id": scope_id},
        )
    else:
        sql = revision.sql if revision is not None else _view_select(store, source_view)
        table = store.create(name=name, sql=sql)

    if partition_column:
        table = store.set_daily_partition(name=name, column=partition_column)
    now = datetime.now(UTC)
    model = CatalogueMaterialization(
        name=name,
        display_name=(display_name or name).strip(),
        description=description,
        query_id=query.id if query else None,
        active_query_revision_id=revision.id if revision else None,
        view_reference_id=source_reference.id if source_reference else None,
        bound_ducklake_view_uuid=source_view.view_uuid if source_view else None,
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


def rebuild(
    session: Session,
    store: MaterializationStore,
    model: CatalogueMaterialization,
    *,
    expected_uuid: UUID,
    target_query_revision_id: UUID | None,
) -> CatalogueMaterializationRecord:
    if model.dematerialization_requested_at is not None:
        raise MaterializationConflictError("This catalogue materialization is being dematerialized.")
    if model.source_state == "source_changing":
        raise MaterializationConflictError(
            "The source view change is incomplete; retry the view edit to reconcile it."
        )
    if target_query_revision_id is not None and model.query_id is None:
        raise MaterializationError("View materializations do not use query revisions.")
    revision_id = target_query_revision_id or model.active_query_revision_id
    revision = session.get(CatalogueQueryRevision, revision_id) if revision_id else None
    if revision is not None and revision.query_id != model.query_id:
        raise LookupError("Saved query revision not found for this materialization.")
    source_reference = (
        session.get(CatalogueViewReference, model.view_reference_id)
        if model.view_reference_id
        else None
    )
    source_view = (
        CatalogueViewStore(store.catalogue).get(source_reference.ducklake_view_uuid)
        if source_reference
        else None
    )
    if revision is None and source_view is None:
        raise LookupError("Catalogue materialization source not found.")
    if model.refresh_mode == "scope_incremental":
        if model.scope_kind != "document" or model.scope_column is None:
            raise MaterializationError(
                "Incremental rebuild requires a document-scoped materialization."
            )
        current = store.inspect(model.name)
        if current.table_uuid != expected_uuid:
            raise MaterializationConflictError(
                "The materialized table changed; refresh the page before retrying."
            )
        scope_id = _seed_scope(store, model.scope_kind)
        sql = (
            revision.sql
            if revision is not None
            else scoped_view_query(
                store.catalogue,
                _require_view(source_view),
                scope_column=model.scope_column,
            )
        )
        store.validate_scoped_schema(
            name=model.name,
            sql=sql,
            parameters={f"{model.scope_kind}_id": scope_id},
        )
        activation_snapshot = store.catalogue.latest_snapshot()
        if activation_snapshot is None:
            raise MaterializationError("DuckLake has no activation snapshot.")
        if revision is not None:
            model.active_query_revision_id = revision.id
        else:
            assert source_view is not None
            model.bound_ducklake_view_uuid = source_view.view_uuid
            model.source_state = "current"
        model.definition_revision_id = uuid4()
        model.activation_snapshot = activation_snapshot
        model.backfill_enabled = True
        session.flush()
        return record(session, store, model)
    sql = revision.sql if revision is not None else _view_select(store, source_view)
    table = store.refresh(name=model.name, expected_uuid=expected_uuid, sql=sql)
    if model.partition_column:
        table = store.set_daily_partition(
            name=model.name, column=model.partition_column
        )
    model.ducklake_table_uuid = table.table_uuid
    if revision is not None:
        model.active_query_revision_id = revision.id
        model.definition_revision_id = uuid4()
    elif source_view is not None:
        model.bound_ducklake_view_uuid = source_view.view_uuid
        model.source_state = "current"
        model.definition_revision_id = uuid4()
    model.last_refreshed_at = datetime.now(UTC)
    session.flush()
    return _record(session, store, model, revision, source_view, table)


def update_maintenance(
    session: Session,
    store: MaterializationStore,
    model: CatalogueMaterialization,
    *,
    live_enabled: bool | None,
    backfill_enabled: bool | None,
    backfill_scopes_per_minute: int | None,
) -> CatalogueMaterializationRecord:
    if model.refresh_mode != "scope_incremental":
        raise MaterializationError("Full-refresh tables have no live maintenance.")
    if model.dematerialization_requested_at is not None:
        raise MaterializationConflictError("This catalogue materialization is being dematerialized.")
    if model.source_state != "current":
        raise MaterializationConflictError(
            "Rebuild the materialization against its changed source before resuming maintenance."
        )
    if live_enabled is not None:
        model.live_enabled = live_enabled
    if backfill_enabled is not None:
        model.backfill_enabled = backfill_enabled
    if backfill_scopes_per_minute is not None:
        model.backfill_scopes_per_minute = backfill_scopes_per_minute
    session.flush()
    return record(session, store, model)


def request_dematerialization(
    session: Session,
    store: MaterializationStore,
    model: CatalogueMaterialization,
    *,
    expected_uuid: UUID,
) -> CatalogueMaterializationRecord:
    if model.dematerialization_requested_at is not None:
        return record(session, store, model)
    current = store.inspect(model.name)
    if current.table_uuid != expected_uuid:
        raise MaterializationConflictError(
            "The materialized table changed; refresh before dematerializing it."
        )
    model.live_enabled = False
    model.backfill_enabled = False
    model.dematerialization_requested_at = datetime.now(UTC)
    session.flush()
    return record(session, store, model)


def record(
    session: Session, store: MaterializationStore, model: CatalogueMaterialization
) -> CatalogueMaterializationRecord:
    revision = (
        session.get(CatalogueQueryRevision, model.active_query_revision_id)
        if model.active_query_revision_id
        else None
    )
    source_reference = (
        session.get(CatalogueViewReference, model.view_reference_id)
        if model.view_reference_id
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
    store: MaterializationStore,
    model: CatalogueMaterialization,
    revision: CatalogueQueryRevision | None,
    source_view: DuckLakeView | None,
    table: MaterializationTable,
) -> CatalogueMaterializationRecord:
    query = (
        session.get(CatalogueQuery, revision.query_id) if revision is not None else None
    )
    completed, failed, last_completed, total = _scope_progress(store, model)
    status = _status(model, completed=completed, failed=failed, total=total)
    return CatalogueMaterializationRecord(
        id=model.id,
        name=model.name,
        qualified_name=f"materialized.{model.name}",
        display_name=model.display_name,
        description=model.description,
        active_query_revision_id=revision.id if revision else None,
        query_id=query.id if query else None,
        query_name=query.name if query else None,
        query_revision=revision.revision if revision else None,
        view_reference_id=model.view_reference_id,
        view_uuid=source_view.view_uuid if source_view else model.bound_ducklake_view_uuid,
        view_name=source_view.qualified_name if source_view else None,
        refresh_mode=model.refresh_mode,
        scope_kind=model.scope_kind,
        activation_snapshot=model.activation_snapshot,
        live_enabled=model.live_enabled,
        backfill_enabled=model.backfill_enabled,
        backfill_scopes_per_minute=model.backfill_scopes_per_minute,
        partition_column=model.partition_column,
        partitioning=list(table.partitioning),
        status=status,
        source_state=model.source_state,
        completed_scopes=completed,
        total_scopes=total,
        failed_scopes=failed,
        last_scope_completed_at=last_completed,
        active_file_count=table.active_file_count,
        active_storage_bytes=table.active_storage_bytes,
        dematerialization_requested_at=model.dematerialization_requested_at,
        ducklake_table_uuid=table.table_uuid,
        row_count=table.row_count,
        columns=[
            CatalogueMaterializationColumn(name=name, data_type=data_type, nullable=nullable)
            for name, data_type, nullable in table.columns
        ],
        last_refreshed_at=model.last_refreshed_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _scope_progress(
    store: MaterializationStore, model: CatalogueMaterialization
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
    model: CatalogueMaterialization, *, completed: int | None, failed: int, total: int | None
) -> str:
    if model.dematerialization_requested_at is not None:
        return "dematerializing"
    if model.source_state == "source_changing":
        return "source_changing"
    if model.source_state == "source_changed":
        return "source_changed"
    if model.refresh_mode == "full":
        return "full_refresh"
    if failed:
        return "degraded"
    if model.backfill_enabled and total is not None and (completed or 0) < total:
        return "backfilling"
    if model.live_enabled:
        return "live"
    return "paused"


def _seed_scope(store: MaterializationStore, scope_kind: str) -> str:
    if scope_kind != "document":
        raise MaterializationError("Only document-scoped materialization is supported.")
    table = _qualified(store, store.catalogue.config.schema, "documents")
    row = store.catalogue.connection.execute(
        f"SELECT document_id FROM {table} LIMIT 1"
    ).fetchone()
    return str(row[0]) if row else str(uuid4())


def _view_select(
    store: MaterializationStore, source_view: DuckLakeView | None
) -> str:
    if source_view is None:
        raise LookupError("DuckLake view not found.")
    alias = store.catalogue.config.alias.replace('"', '""')
    name = source_view.view_name.replace('"', '""')
    return f'SELECT * FROM "{alias}"."views"."{name}"'


def _require_view(source_view: DuckLakeView | None) -> DuckLakeView:
    if source_view is None:
        raise LookupError("DuckLake view not found.")
    return source_view


def _qualified(store: MaterializationStore, schema: str, table: str) -> str:
    return ".".join(
        '"' + value.replace('"', '""') + '"'
        for value in (store.catalogue.config.alias, schema, table)
    )
