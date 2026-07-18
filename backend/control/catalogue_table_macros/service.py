"""Coordinate authoritative Postgres definitions with executable DuckLake macros."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from repository.catalogue.table_macros import (
    TABLE_MACRO_SCHEMA,
    CatalogueTableMacroConflictError,
    CatalogueTableMacroStore,
)

from .models import CatalogueTableMacroDefinition
from .schemas import CatalogueTableMacroRecord


def list_records(
    session: Session, store: CatalogueTableMacroStore
) -> list[CatalogueTableMacroRecord]:
    definitions = list(
        session.scalars(
            select(CatalogueTableMacroDefinition).order_by(
                CatalogueTableMacroDefinition.macro_name
            )
        )
    )
    available = {macro.macro_name for macro in store.list()}
    return [_record(definition, definition.macro_name in available) for definition in definitions]


def get_definition(
    session: Session, definition_id: UUID
) -> CatalogueTableMacroDefinition | None:
    return session.get(CatalogueTableMacroDefinition, definition_id)


def get_record(
    session: Session, store: CatalogueTableMacroStore, definition_id: UUID
) -> CatalogueTableMacroRecord | None:
    definition = get_definition(session, definition_id)
    return (
        _record(definition, store.get(definition.macro_name) is not None)
        if definition is not None
        else None
    )


def create_definition(
    session: Session,
    store: CatalogueTableMacroStore,
    *,
    slug: str,
    parameters: list[str],
    parameter_defaults: dict[str, str] | None = None,
    sql: str,
    description: str | None,
    created_from_query_revision_id: UUID | None = None,
) -> CatalogueTableMacroRecord:
    existing = session.scalar(
        select(CatalogueTableMacroDefinition).where(
            CatalogueTableMacroDefinition.schema_name == TABLE_MACRO_SCHEMA,
            CatalogueTableMacroDefinition.macro_name == slug,
        )
    )
    if existing is not None:
        raise CatalogueTableMacroConflictError(
            f"Table macro {TABLE_MACRO_SCHEMA}.{slug} is already managed by Atlas."
        )
    defaults = parameter_defaults or {}
    macro = store.create(
        name=slug,
        parameters=parameters,
        parameter_defaults=defaults,
        sql=sql,
    )
    definition = CatalogueTableMacroDefinition(
        schema_name=TABLE_MACRO_SCHEMA,
        macro_name=slug,
        slug=slug,
        description=description,
        parameters=list(macro.parameters),
        parameter_defaults=defaults,
        sql=sql.strip(),
        created_from_query_revision_id=created_from_query_revision_id,
    )
    session.add(definition)
    try:
        session.flush()
    except IntegrityError as exc:
        raise CatalogueTableMacroConflictError(
            "A table macro with this slug already exists."
        ) from exc
    return _record(definition, True)


def update_definition(
    session: Session,
    store: CatalogueTableMacroStore,
    definition: CatalogueTableMacroDefinition,
    *,
    expected_revision_id: UUID,
    parameters: list[str],
    parameter_defaults: dict[str, str] | None = None,
    sql: str,
    slug: str,
    description: str | None,
) -> CatalogueTableMacroRecord:
    locked = session.scalar(
        select(CatalogueTableMacroDefinition)
        .where(CatalogueTableMacroDefinition.id == definition.id)
        .with_for_update()
    )
    if locked is None:
        raise CatalogueTableMacroConflictError("The table macro definition no longer exists.")
    if locked.definition_revision_id != expected_revision_id:
        raise CatalogueTableMacroConflictError(
            "The table macro changed; refresh before editing."
        )
    defaults = parameter_defaults or {}
    macro = store.replace(
        name=locked.macro_name,
        parameters=parameters,
        parameter_defaults=defaults,
        sql=sql,
    )
    locked.parameters = list(macro.parameters)
    locked.parameter_defaults = defaults
    locked.sql = sql.strip()
    locked.slug = slug
    locked.description = description
    locked.definition_revision_id = uuid4()
    try:
        session.flush()
    except IntegrityError as exc:
        raise CatalogueTableMacroConflictError(
            "A table macro with this slug already exists."
        ) from exc
    return _record(locked, True)


def drop_definition(
    session: Session,
    store: CatalogueTableMacroStore,
    definition: CatalogueTableMacroDefinition,
    *,
    expected_revision_id: UUID,
) -> None:
    locked = session.scalar(
        select(CatalogueTableMacroDefinition)
        .where(CatalogueTableMacroDefinition.id == definition.id)
        .with_for_update()
    )
    if locked is None:
        raise CatalogueTableMacroConflictError("The table macro definition no longer exists.")
    if locked.definition_revision_id != expected_revision_id:
        raise CatalogueTableMacroConflictError(
            "The table macro changed; refresh before deleting."
        )
    store.drop(name=locked.macro_name)
    session.delete(locked)
    session.flush()


def _record(
    definition: CatalogueTableMacroDefinition, available: bool
) -> CatalogueTableMacroRecord:
    return CatalogueTableMacroRecord(
        id=definition.id,
        schema_name=definition.schema_name,
        macro_name=definition.macro_name,
        qualified_name=f"{definition.schema_name}.{definition.macro_name}",
        slug=definition.slug,
        description=definition.description,
        parameters=list(definition.parameters),
        parameter_defaults=dict(definition.parameter_defaults),
        sql=definition.sql,
        definition_revision_id=definition.definition_revision_id,
        fixture_path=definition.fixture_path,
        available=available,
        created_from_query_revision_id=definition.created_from_query_revision_id,
        created_at=definition.created_at,
        updated_at=definition.updated_at,
    )
