"""Coordinate scalar-macro control state with executable DuckLake macros."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from repository.catalogue.scalar_macros import (
    SCALAR_MACRO_SCHEMA,
    CatalogueScalarMacroConflictError,
    CatalogueScalarMacroStore,
)
from repository.catalogue.definition_compiler import (
    compile_definition_authoring,
    store_compilation,
)

from .models import CatalogueScalarMacroDefinition
from .schemas import CatalogueScalarMacroRecord


def list_records(
    session: Session, store: CatalogueScalarMacroStore
) -> list[CatalogueScalarMacroRecord]:
    definitions = list(
        session.scalars(
            select(CatalogueScalarMacroDefinition).order_by(
                CatalogueScalarMacroDefinition.macro_name
            )
        )
    )
    available = {macro.macro_name for macro in store.list()}
    return [_record(item, item.macro_name in available) for item in definitions]


def get_definition(
    session: Session, definition_id: UUID
) -> CatalogueScalarMacroDefinition | None:
    return session.get(CatalogueScalarMacroDefinition, definition_id)


def get_record(
    session: Session, store: CatalogueScalarMacroStore, definition_id: UUID
) -> CatalogueScalarMacroRecord | None:
    definition = get_definition(session, definition_id)
    return (
        _record(definition, store.get(definition.macro_name) is not None)
        if definition is not None
        else None
    )


def create_definition(
    session: Session,
    store: CatalogueScalarMacroStore,
    *,
    slug: str,
    parameters: list[str],
    sql: str,
    description: str | None,
) -> CatalogueScalarMacroRecord:
    compilation = compile_definition_authoring(
        store.catalogue,
        sql,
        kind="scalar_macro",
        schema_name=SCALAR_MACRO_SCHEMA,
        object_name=slug,
        parameters=tuple(parameters),
    )
    existing = session.scalar(
        select(CatalogueScalarMacroDefinition).where(
            CatalogueScalarMacroDefinition.schema_name == SCALAR_MACRO_SCHEMA,
            CatalogueScalarMacroDefinition.macro_name == slug,
        )
    )
    if existing is not None:
        raise CatalogueScalarMacroConflictError(
            f"Scalar macro {SCALAR_MACRO_SCHEMA}.{slug} is already managed by Atlas."
        )
    macro = store.create(name=slug, parameters=parameters, sql=sql)
    definition = CatalogueScalarMacroDefinition(
        schema_name=SCALAR_MACRO_SCHEMA,
        macro_name=slug,
        slug=slug,
        description=description,
        parameters=list(macro.parameters),
        sql=sql.strip(),
    )
    store_compilation(definition, compilation)
    session.add(definition)
    try:
        session.flush()
    except IntegrityError as exc:
        raise CatalogueScalarMacroConflictError(
            "A scalar macro with this slug already exists."
        ) from exc
    return _record(definition, True)


def update_definition(
    session: Session,
    store: CatalogueScalarMacroStore,
    definition: CatalogueScalarMacroDefinition,
    *,
    expected_revision_id: UUID,
    parameters: list[str],
    sql: str,
    slug: str,
    description: str | None,
) -> CatalogueScalarMacroRecord:
    locked = session.scalar(
        select(CatalogueScalarMacroDefinition)
        .where(CatalogueScalarMacroDefinition.id == definition.id)
        .with_for_update()
    )
    if locked is None:
        raise CatalogueScalarMacroConflictError("The scalar macro no longer exists.")
    if locked.fixture_path is not None:
        raise CatalogueScalarMacroConflictError("System scalar macros are read-only.")
    if locked.definition_revision_id != expected_revision_id:
        raise CatalogueScalarMacroConflictError(
            "The scalar macro changed; refresh before editing."
        )
    compilation = compile_definition_authoring(
        store.catalogue,
        sql,
        kind="scalar_macro",
        schema_name=SCALAR_MACRO_SCHEMA,
        object_name=locked.macro_name,
        parameters=tuple(parameters),
    )
    macro = store.replace(name=locked.macro_name, parameters=parameters, sql=sql)
    locked.parameters = list(macro.parameters)
    locked.sql = sql.strip()
    locked.slug = slug
    locked.description = description
    locked.definition_revision_id = uuid4()
    store_compilation(locked, compilation)
    try:
        session.flush()
    except IntegrityError as exc:
        raise CatalogueScalarMacroConflictError(
            "A scalar macro with this slug already exists."
        ) from exc
    return _record(locked, True)


def drop_definition(
    session: Session,
    store: CatalogueScalarMacroStore,
    definition: CatalogueScalarMacroDefinition,
    *,
    expected_revision_id: UUID,
) -> None:
    locked = session.scalar(
        select(CatalogueScalarMacroDefinition)
        .where(CatalogueScalarMacroDefinition.id == definition.id)
        .with_for_update()
    )
    if locked is None:
        raise CatalogueScalarMacroConflictError("The scalar macro no longer exists.")
    if locked.fixture_path is not None:
        raise CatalogueScalarMacroConflictError("System scalar macros cannot be deleted.")
    if locked.definition_revision_id != expected_revision_id:
        raise CatalogueScalarMacroConflictError(
            "The scalar macro changed; refresh before deleting."
        )
    store.drop(name=locked.macro_name)
    session.delete(locked)
    session.flush()


def _record(
    definition: CatalogueScalarMacroDefinition, available: bool
) -> CatalogueScalarMacroRecord:
    return CatalogueScalarMacroRecord(
        id=definition.id,
        schema_name=definition.schema_name,
        macro_name=definition.macro_name,
        qualified_name=f"{definition.schema_name}.{definition.macro_name}",
        slug=definition.slug,
        description=definition.description,
        parameters=list(definition.parameters),
        sql=definition.sql,
        definition_revision_id=definition.definition_revision_id,
        fixture_path=definition.fixture_path,
        available=available,
        created_at=definition.created_at,
        updated_at=definition.updated_at,
        compiler_outcome=definition.compiler_outcome,
        compiler_diagnostics=definition.compiler_diagnostics or [],
        compiler_dependencies=definition.compiler_dependencies or [],
        compiler_version=definition.compiler_version,
        catalogue_definition_revision=definition.catalogue_definition_revision,
    )
