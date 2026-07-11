from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from control.catalogue_queries.models import CatalogueQuery, CatalogueQueryRevision
from repository.catalogue.materialized_views import MaterializedTable, MaterializedViewStore

from .models import MaterializedView
from .schemas import MaterializedViewColumn, MaterializedViewRecord


def list_records(session: Session, store: MaterializedViewStore) -> list[MaterializedViewRecord]:
    models = session.scalars(select(MaterializedView).where(MaterializedView.archived_at.is_(None)).order_by(MaterializedView.updated_at.desc()))
    return [record(session, store, model) for model in models]


def get_model(session: Session, view_id: UUID) -> MaterializedView | None:
    model = session.get(MaterializedView, view_id)
    return model if model is not None and model.archived_at is None else None


def create(session: Session, store: MaterializedViewStore, *, name: str, display_name: str | None, description: str | None, query_revision_id: UUID) -> MaterializedViewRecord:
    revision = session.get(CatalogueQueryRevision, query_revision_id)
    if revision is None:
        raise LookupError("Saved query revision not found.")
    table = store.create(name=name, sql=revision.sql)
    now = datetime.now(UTC)
    model = MaterializedView(
        name=name,
        display_name=(display_name or name).strip(),
        description=description,
        query_revision_id=revision.id,
        ducklake_table_uuid=table.table_uuid,
        last_refreshed_at=now,
    )
    session.add(model)
    session.flush()
    return _record(model, revision, session.get(CatalogueQuery, revision.query_id), table)


def refresh(session: Session, store: MaterializedViewStore, model: MaterializedView, *, expected_uuid: UUID) -> MaterializedViewRecord:
    revision = session.get(CatalogueQueryRevision, model.query_revision_id)
    if revision is None:
        raise LookupError("Saved query revision not found.")
    table = store.refresh(name=model.name, expected_uuid=expected_uuid, sql=revision.sql)
    model.ducklake_table_uuid = table.table_uuid
    model.last_refreshed_at = datetime.now(UTC)
    session.flush()
    return _record(model, revision, session.get(CatalogueQuery, revision.query_id), table)


def drop(session: Session, store: MaterializedViewStore, model: MaterializedView, *, expected_uuid: UUID) -> None:
    store.drop(name=model.name, expected_uuid=expected_uuid)
    model.archived_at = datetime.now(UTC)
    session.flush()


def record(session: Session, store: MaterializedViewStore, model: MaterializedView) -> MaterializedViewRecord:
    revision = session.get(CatalogueQueryRevision, model.query_revision_id)
    if revision is None:
        raise RuntimeError("Materialized view query revision is missing.")
    query = session.get(CatalogueQuery, revision.query_id)
    return _record(model, revision, query, store.inspect(model.name))


def _record(model: MaterializedView, revision: CatalogueQueryRevision, query: CatalogueQuery | None, table: MaterializedTable) -> MaterializedViewRecord:
    if query is None:
        raise RuntimeError("Materialized view saved query is missing.")
    return MaterializedViewRecord(
        id=model.id,
        name=model.name,
        qualified_name=f"materialized.{model.name}",
        display_name=model.display_name,
        description=model.description,
        query_revision_id=revision.id,
        query_id=query.id,
        query_name=query.name,
        query_revision=revision.revision,
        ducklake_table_uuid=table.table_uuid,
        row_count=table.row_count,
        columns=[MaterializedViewColumn(name=name, data_type=data_type, nullable=nullable) for name, data_type, nullable in table.columns],
        last_refreshed_at=model.last_refreshed_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )
