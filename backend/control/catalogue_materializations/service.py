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
)
from repository.catalogue.compiler_definitions import (
    read_catalogue_compiler_definitions,
)
from repository.catalogue.views import CatalogueViewStore
from runtime.catalogue_events import materialization_durable

from .models import CatalogueMaterialization
from .schemas import (
    CatalogueMaterializationRecord,
    CatalogueMaterializationSummary,
    MaterializationEligibilityDiagnostic,
    ViewMaterializationEligibility,
)


def materialization_store(session: Session, catalogue) -> MaterializationStore:
    """Build a store from the same authoritative macro definitions as execution."""

    with catalogue.remote_transaction():
        definitions = read_catalogue_compiler_definitions(
            catalogue.trusted_connection,
            catalogue_alias=catalogue.config.alias,
        )
    return MaterializationStore(
        catalogue,
        scalar_macros=definitions.scalar_macros,
        table_macros=definitions.table_macros,
        views=definitions.views,
        scalar_functions=definitions.scalar_functions,
    )


def materialization_eligibility(
    session: Session,
    store: MaterializationStore,
    *,
    view_reference_id: UUID,
    source_table: str,
    refresh_strategy: str,
    key_columns: list[str],
) -> ViewMaterializationEligibility:
    reference = session.scalar(
        select(CatalogueViewReference).where(
            CatalogueViewReference.id == view_reference_id,
            CatalogueViewReference.archived_at.is_(None),
        )
    )
    if reference is None:
        raise LookupError("Managed view not found.")
    source_view = CatalogueViewStore(store.catalogue).get(
        reference.ducklake_view_uuid
    )
    if source_view is None:
        raise LookupError("DuckLake view not found.")
    try:
        source = store.table_identity(source_table)
        store.validate_refresh_strategy(
            source_table=source.table_name,
            sql=source_view.sql,
            refresh_strategy=refresh_strategy,
            key_columns=tuple(key_columns),
            coverage_source=None,
        )
    except MaterializationError as exc:
        return ViewMaterializationEligibility(
            eligible=False,
            diagnostics=[
                MaterializationEligibilityDiagnostic(
                    code="materialization_incompatible",
                    severity="error",
                    message=str(exc),
                )
            ],
        )
    return ViewMaterializationEligibility(eligible=True, diagnostics=[])


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


def unavailable_materialized_views(
    session: Session,
    view_names: frozenset[str],
) -> list[tuple[str, str, str | None]]:
    """Return requested views whose intended physical incarnation is unavailable."""

    if not view_names:
        return []
    rows = session.execute(
        select(
            CatalogueViewReference.view_name,
            CatalogueMaterialization.observed_state,
            CatalogueMaterialization.last_error,
        )
        .join(
            CatalogueViewReference,
            CatalogueViewReference.id
            == CatalogueMaterialization.view_reference_id,
        )
        .where(
            CatalogueMaterialization.archived_at.is_(None),
            CatalogueViewReference.view_name.in_(view_names),
            CatalogueMaterialization.observed_state.not_in(("live", "paused")),
        )
        .order_by(CatalogueViewReference.view_name)
    )
    return [
        (str(view_name), str(observed_state), last_error)
        for view_name, observed_state, last_error in rows
    ]


def summary(model: CatalogueMaterialization) -> CatalogueMaterializationSummary:
    return CatalogueMaterializationSummary(
        id=model.id,
        status=model.observed_state,
    )


def put_for_view(
    session: Session,
    store: MaterializationStore,
    *,
    view_reference_id: UUID,
    name: str,
    display_name: str | None,
    description: str | None,
    source_table: str,
    refresh_strategy: str,
    key_columns: list[str],
    refresh_delay_seconds: float,
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
        if (
            existing.name != name
            or existing.source_table != source_table
            or existing.refresh_strategy != refresh_strategy
            or existing.key_columns != key_columns
            or existing.partition_column != partition_column
        ):
            raise MaterializationConflictError(
                "This view already has a different materialization incarnation. "
                "Dematerialize it before changing the definition."
            )
        return record(session, existing)
    source_view = CatalogueViewStore(store.catalogue).get(reference.ducklake_view_uuid)
    if source_view is None:
        raise LookupError("DuckLake view not found.")
    source = store.table_identity(source_table)
    store.validate_refresh_strategy(
        source_table=source.table_name,
        sql=source_view.sql,
        refresh_strategy=refresh_strategy,
        key_columns=tuple(key_columns),
        coverage_source="materialization_create",
    )
    control_snapshot = store.catalogue.latest_snapshot()
    if control_snapshot is None:
        raise MaterializationConflictError("DuckLake has no control snapshot.")
    materialization_id = uuid4()
    model = CatalogueMaterialization(
        id=materialization_id,
        name=name,
        display_name=(display_name or name).strip(),
        description=description,
        source_sql=source_view.sql,
        view_reference_id=reference.id,
        source_view_uuid=source_view.view_uuid,
        source_table=source.table_name,
        source_table_id=source.table_id,
        source_table_uuid=source.table_uuid,
        control_snapshot=control_snapshot,
        desired_state="live",
        observed_state="creating",
        nats_consumer_name=materialization_durable(materialization_id),
        refresh_delay_seconds=refresh_delay_seconds,
        refresh_strategy=refresh_strategy,
        key_columns=key_columns,
        partition_column=partition_column,
    )
    session.add(model)
    session.flush()
    return record(session, model)


def update_state(
    session: Session,
    model: CatalogueMaterialization,
    *,
    desired_state: str | None,
    refresh_delay_seconds: float | None,
) -> CatalogueMaterializationRecord:
    if model.desired_state == "deleting":
        raise MaterializationConflictError("This materialization is being removed.")
    if desired_state is not None and model.observed_state in {
        "creating",
        "backfilling",
    }:
        raise MaterializationConflictError(
            "A materialization cannot be paused while its initial backfill is running."
        )
    if desired_state is not None:
        if desired_state == "live" and model.observed_state == "blocked_schema":
            raise MaterializationConflictError(
                "This incarnation has an incompatible schema and cannot resume. "
                "Dematerialize it and create a new one."
            )
        model.desired_state = desired_state
        if desired_state == "live" and model.observed_state == "failed":
            if model.ducklake_table_uuid is None:
                model.observed_state = "creating"
            elif model.bootstrap_partition_count is not None:
                model.observed_state = "backfilling"
            else:
                model.observed_state = "live"
            model.last_error = None
    if refresh_delay_seconds is not None:
        model.refresh_delay_seconds = refresh_delay_seconds
    session.flush()
    return record(session, model)


def request_dematerialization(
    session: Session,
    model: CatalogueMaterialization,
) -> CatalogueMaterializationRecord:
    model.desired_state = "deleting"
    model.observed_state = "deleting"
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
            if reference is not None
            else model.source_view_uuid
        ),
        view_name=(
            f"{reference.schema_name}.{reference.view_name}"
            if reference is not None
            else f"views.{model.name}"
        ),
        source_table=model.source_table,
        source_table_id=model.source_table_id,
        source_table_uuid=model.source_table_uuid,
        control_snapshot=model.control_snapshot,
        desired_state=model.desired_state,
        observed_state=model.observed_state,
        nats_consumer_name=model.nats_consumer_name,
        refresh_delay_seconds=model.refresh_delay_seconds,
        refresh_strategy=model.refresh_strategy,
        key_columns=model.key_columns,
        partition_column=model.partition_column,
        target_table_id=model.target_table_id,
        ducklake_table_uuid=model.ducklake_table_uuid,
        bootstrap_snapshot=model.bootstrap_snapshot,
        bootstrap_partition_count=model.bootstrap_partition_count,
        bootstrap_partition_cursor=model.bootstrap_partition_cursor,
        processed_snapshot=model.processed_snapshot,
        last_refreshed_at=model.last_refreshed_at,
        last_error=model.last_error,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def mark_failed(
    session: Session, materialization_id: UUID, error: Exception
) -> None:
    model = get_model(session, materialization_id)
    if model is None or model.desired_state == "deleting":
        return
    model.observed_state = "failed"
    model.last_error = str(error)[:4000]
    model.updated_at = datetime.now(UTC)
