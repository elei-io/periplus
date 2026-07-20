"""Idempotent SQL fixture seeding for user-visible catalogue definitions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlglot import exp, parse
from sqlglot.errors import ParseError

from control.catalogue_queries.models import CatalogueQuery
from control.catalogue_queries.service import create_query, detail, restore_query, update_query
from control.catalogue_materializations.models import CatalogueMaterialization
from control.catalogue_materializations.service import (
    put_for_view as materialize_view,
    rebuild as rebuild_materialization,
)
from control.catalogue_scalar_macros.models import CatalogueScalarMacroDefinition
from control.catalogue_scalar_macros.service import (
    create_definition as create_scalar_macro,
)
from control.catalogue_table_macros.models import CatalogueTableMacroDefinition
from control.catalogue_table_macros.service import (
    create_definition as create_macro,
    drop_definition as drop_macro,
    update_definition as update_macro,
)
from control.catalogue_views.models import CatalogueViewReference
from control.catalogue_views.service import (
    create_reference as create_view,
    get_record as get_view_record,
    update_reference as update_view,
)
from repository.catalogue.client import Catalogue
from repository.catalogue.materializations import (
    MaterializationSchemaChangeError,
    MaterializationStore,
)
from repository.catalogue.query import CatalogueQueryError, classify_select
from repository.catalogue.scalar_macros import (
    SCALAR_MACRO_SCHEMA,
    CatalogueScalarMacroStore,
)
from repository.catalogue.table_macros import (
    TABLE_MACRO_SCHEMA,
    CatalogueTableMacroStore,
)
from repository.catalogue.views import CatalogueViewStore, VIEW_SCHEMA


class CatalogueFixtureError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RelationFixture:
    fixture_path: str
    name: str
    sql: str
    parameters: tuple[str, ...] = ()
    parameter_defaults: tuple[tuple[str, str], ...] = ()
    partition_column: str | None = None


_FIXTURE_PARAMETER_DEFAULT = re.compile(
    r"(?P<name>[a-z_][a-z0-9_]*)\s*:=\s*"
    r"(?P<expression>NULL\s*::\s*[a-z_][a-z0-9_]*|'(?:''|[^'])*')",
    re.IGNORECASE,
)
_FIXTURE_DAILY_PARTITION = re.compile(
    r"^\s*--\s*atlas:partition-by-day\s*=\s*"
    r"(?P<column>[a-z_][a-z0-9_]*)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def seed_catalogue_fixtures(
    session: Session, catalogue: Catalogue, fixtures_root: Path
) -> None:
    """Seed every SQL fixture without overwriting a user-owned name collision."""

    seed_system_catalogue_fixtures(session, catalogue, fixtures_root)
    macro_store = CatalogueTableMacroStore(catalogue)
    view_store = CatalogueViewStore(catalogue)
    for path in _sql_files(fixtures_root / "queries"):
        _seed_query(session, _parse_query_fixture(fixtures_root, path))
    for path in _sql_files(fixtures_root / "views"):
        _seed_view(
            session,
            view_store,
            _parse_relation_fixture(fixtures_root, path, kind="VIEW", schema=VIEW_SCHEMA),
        )
    materialized_fixtures: list[tuple[str, RelationFixture]] = []
    for scope_kind in ("document", "crawl"):
        for path in _sql_files(fixtures_root / "materialized_views" / scope_kind):
            fixture = _parse_relation_fixture(
                fixtures_root,
                path,
                kind="VIEW",
                schema=VIEW_SCHEMA,
                materialized=True,
            )
            _seed_view(session, view_store, fixture)
            materialized_fixtures.append((scope_kind, fixture))
    for scope_kind, fixture in materialized_fixtures:
        _seed_materialization(
            session,
            catalogue,
            fixture,
            scope_kind=scope_kind,
        )
    macro_paths = _sql_files(fixtures_root / "table_macros")
    for path in _ordered_table_macro_paths(macro_paths):
        _seed_macro(
            session,
            macro_store,
            _parse_relation_fixture(
                fixtures_root, path, kind="MACRO", schema=TABLE_MACRO_SCHEMA
            ),
        )
    _drop_retired_macros(
        session,
        macro_store,
        active_fixture_paths={
            path.relative_to(fixtures_root).as_posix() for path in macro_paths
        },
    )
    catalogue.validate_schema()


def seed_system_catalogue_fixtures(
    session: Session, catalogue: Catalogue, fixtures_root: Path
) -> None:
    """Install fixed system SQL fixtures before dependent catalogue fixtures."""

    directory = fixtures_root / "scalar_macros"
    store = CatalogueScalarMacroStore(catalogue)
    paths = _sql_files(directory)
    for path in paths:
        source = path.read_text(encoding="utf-8").strip()
        try:
            statements = [
                statement for statement in parse(source, dialect="duckdb") if statement
            ]
        except ParseError as exc:
            raise CatalogueFixtureError(
                f"Invalid scalar macro fixture {path}: {exc}"
            ) from exc
        if len(statements) != 1 or not isinstance(statements[0], exp.Create):
            raise CatalogueFixtureError(
                f"{path} must contain exactly one CREATE OR REPLACE MACRO statement."
            )
        statement = statements[0]
        if (
            str(statement.args.get("kind", "")).upper() != "MACRO"
            or not statement.args.get("replace")
            or next(statement.find_all(exp.ReturnsProperty), None) is not None
        ):
            raise CatalogueFixtureError(
                f"{path} must contain exactly one CREATE OR REPLACE scalar MACRO."
            )
        target = statement.this
        if not isinstance(target, exp.UserDefinedFunction):
            raise CatalogueFixtureError(f"{path} has an invalid scalar macro signature.")
        name = target.this
        if not isinstance(name, exp.Table) or name.db:
            raise CatalogueFixtureError(
                f"{path} must declare an unqualified scalar macro name."
            )
        if name.name != path.stem:
            raise CatalogueFixtureError(
                f"{path} must declare {path.stem}, not {name.name}."
            )
        fixture_path = path.relative_to(fixtures_root).as_posix()
        if not all(isinstance(parameter, exp.Identifier) for parameter in target.expressions):
            raise CatalogueFixtureError(
                f"{path} scalar macro parameters must be simple identifiers."
            )
        parameters = [parameter.name for parameter in target.expressions]
        expression = statement.expression
        if expression is None:
            raise CatalogueFixtureError(f"{path} has no scalar macro expression.")
        sql = expression.unnest().sql(dialect="duckdb")
        existing = session.scalar(
            select(CatalogueScalarMacroDefinition).where(
                CatalogueScalarMacroDefinition.fixture_path == fixture_path
            )
        )
        if existing is None:
            collision = session.scalar(
                select(CatalogueScalarMacroDefinition).where(
                    CatalogueScalarMacroDefinition.schema_name == SCALAR_MACRO_SCHEMA,
                    CatalogueScalarMacroDefinition.macro_name == path.stem,
                )
            )
            if collision is not None:
                raise CatalogueFixtureError(
                    f"{fixture_path} conflicts with user-owned scalar macro "
                    f"{SCALAR_MACRO_SCHEMA}.{path.stem}."
                )
            created = create_scalar_macro(
                session,
                store,
                slug=path.stem,
                parameters=parameters,
                sql=sql,
                description=None,
            )
            existing = session.get(CatalogueScalarMacroDefinition, created.id)
            if existing is None:
                raise RuntimeError("Seeded scalar macro was not found after creation.")
            existing.fixture_path = fixture_path
            session.flush()
        elif (
            existing.parameters != parameters
            or _canonical_expression(existing.sql) != _canonical_expression(sql)
            or store.get(existing.macro_name) is None
        ):
            macro = store.replace(
                name=existing.macro_name, parameters=parameters, sql=sql
            )
            existing.parameters = list(macro.parameters)
            existing.sql = sql
            existing.definition_revision_id = uuid4()
            session.flush()
    active_fixture_paths = {
        path.relative_to(fixtures_root).as_posix() for path in paths
    }
    retired = list(
        session.scalars(
            select(CatalogueScalarMacroDefinition).where(
                CatalogueScalarMacroDefinition.fixture_path.is_not(None),
                CatalogueScalarMacroDefinition.fixture_path.not_in(
                    active_fixture_paths
                ),
            )
        )
    )
    for definition in retired:
        store.drop(name=definition.macro_name)
        session.delete(definition)
    session.flush()


def _drop_retired_macros(
    session: Session,
    store: CatalogueTableMacroStore,
    *,
    active_fixture_paths: set[str],
) -> None:
    retired = list(
        session.scalars(
            select(CatalogueTableMacroDefinition).where(
                CatalogueTableMacroDefinition.fixture_path.is_not(None),
                CatalogueTableMacroDefinition.fixture_path.not_in(active_fixture_paths),
            )
        )
    )
    for definition in retired:
        drop_macro(
            session,
            store,
            definition,
            expected_revision_id=definition.definition_revision_id,
        )


def _seed_query(session: Session, fixture: RelationFixture) -> None:
    existing = session.scalar(
        select(CatalogueQuery).where(CatalogueQuery.fixture_path == fixture.fixture_path)
    )
    if existing is None:
        collisions = list(
            session.scalars(
                select(CatalogueQuery).where(
                    CatalogueQuery.slug == fixture.name,
                    CatalogueQuery.archived_at.is_(None),
                )
            )
        )
        if collisions:
            raise CatalogueFixtureError(
                f"{fixture.fixture_path} conflicts with user-owned saved query "
                f"{fixture.name!r}."
            )
        created = create_query(
            session,
            slug=fixture.name,
            description=None,
            sql=fixture.sql,
            change_note=f"Seeded from {fixture.fixture_path}",
        )
        existing = session.get(CatalogueQuery, created.id)
        if existing is None:
            raise RuntimeError("Seeded saved query was not found after creation.")
        existing.fixture_path = fixture.fixture_path
        session.flush()
        return

    if existing.slug != fixture.name:
        raise CatalogueFixtureError(
            f"{fixture.fixture_path} changed its saved query name from "
            f"{existing.slug!r} to {fixture.name!r}; use a new fixture filename."
        )
    if existing.archived_at is not None:
        restore_query(session, existing)
    current = detail(existing)
    if _canonical(current.sql) != _canonical(fixture.sql):
        update_query(
            session,
            existing,
            expected_revision_id=current.current_revision_id,
            sql=fixture.sql,
            slug=fixture.name,
            description=None,
            change_note=f"Updated from {fixture.fixture_path}",
        )


def _seed_view(
    session: Session, store: CatalogueViewStore, fixture: RelationFixture
) -> None:
    existing = session.scalar(
        select(CatalogueViewReference).where(
            CatalogueViewReference.fixture_path == fixture.fixture_path
        )
    )
    if existing is None:
        collision = session.scalar(
            select(CatalogueViewReference).where(
                CatalogueViewReference.schema_name == VIEW_SCHEMA,
                CatalogueViewReference.view_name == fixture.name,
                CatalogueViewReference.archived_at.is_(None),
            )
        )
        if collision is not None:
            raise CatalogueFixtureError(
                f"{fixture.fixture_path} conflicts with user-owned view "
                f"{VIEW_SCHEMA}.{fixture.name}."
            )
        created = create_view(
            session,
            store,
            slug=fixture.name,
            sql=fixture.sql,
            description=None,
        )
        if created.id is None:
            raise RuntimeError("Seeded view did not receive an Atlas reference.")
        existing = session.get(CatalogueViewReference, created.id)
        if existing is None:
            raise RuntimeError("Seeded view reference was not found after creation.")
        existing.fixture_path = fixture.fixture_path
        session.flush()
        return

    if existing.view_name != fixture.name:
        raise CatalogueFixtureError(
            f"{fixture.fixture_path} changed its view name from "
            f"{existing.view_name!r} to {fixture.name!r}; use a new fixture filename."
        )
    current = get_view_record(session, store, existing.id)
    if current is None or not current.available:
        raise CatalogueFixtureError(
            f"Fixture-owned view {VIEW_SCHEMA}.{fixture.name} is missing from DuckLake."
        )
    if _canonical(current.sql) != _canonical(fixture.sql):
        update_view(
            session,
            store,
            existing,
            expected_uuid=current.ducklake_view_uuid,
            sql=fixture.sql,
            slug=fixture.name,
            description=None,
        )


def _seed_materialization(
    session: Session,
    catalogue: Catalogue,
    fixture: RelationFixture,
    *,
    scope_kind: str,
) -> None:
    reference = session.scalar(
        select(CatalogueViewReference).where(
            CatalogueViewReference.fixture_path == fixture.fixture_path
        )
    )
    if reference is None:
        raise RuntimeError("Seeded materialized view reference was not found.")
    scope_column = f"{scope_kind}_id"
    existing = session.scalar(
        select(CatalogueMaterialization).where(
            CatalogueMaterialization.view_reference_id == reference.id,
            CatalogueMaterialization.archived_at.is_(None),
        )
    )
    store = MaterializationStore(catalogue)
    if existing is None:
        _create_seeded_materialization(
            session, store, reference, fixture, scope_kind=scope_kind
        )
        return
    if existing.scope_kind != scope_kind or existing.scope_column != scope_column:
        raise CatalogueFixtureError(
            f"{fixture.fixture_path} changed its materialization scope from "
            f"{existing.scope_kind}:{existing.scope_column} to "
            f"{scope_kind}:{scope_column}; use a new fixture filename."
        )
    if existing.partition_column != fixture.partition_column:
        _replace_seeded_materialization(
            session,
            catalogue,
            store,
            reference,
            fixture,
            existing,
            scope_kind=scope_kind,
        )
        return
    if existing.source_state == "source_changed":
        try:
            rebuild_materialization(
                session,
                store,
                existing,
                expected_uuid=existing.ducklake_table_uuid,
            )
        except MaterializationSchemaChangeError:
            _replace_seeded_materialization(
                session,
                catalogue,
                store,
                reference,
                fixture,
                existing,
                scope_kind=scope_kind,
            )


def _create_seeded_materialization(
    session: Session,
    store: MaterializationStore,
    reference: CatalogueViewReference,
    fixture: RelationFixture,
    *,
    scope_kind: str,
) -> None:
    created = materialize_view(
        session,
        store,
        view_reference_id=reference.id,
        name=fixture.name,
        display_name=None,
        description=None,
        scope_kind=scope_kind,
        scope_column=f"{scope_kind}_id",
        backfill_scopes_per_minute=60,
        partition_column=fixture.partition_column,
    )
    model = session.get(CatalogueMaterialization, created.id)
    if model is None:
        raise RuntimeError("Seeded materialization was not found after creation.")
    # DuckLake returns a catalogue-qualified form of a newly created view.
    # The fixture itself remains the authoritative, portable source query.
    model.source_sql = fixture.sql
    session.flush()


def _replace_seeded_materialization(
    session: Session,
    catalogue: Catalogue,
    store: MaterializationStore,
    reference: CatalogueViewReference,
    fixture: RelationFixture,
    existing: CatalogueMaterialization,
    *,
    scope_kind: str,
) -> None:
    restored = CatalogueViewStore(catalogue).replace(
        current_uuid=reference.ducklake_view_uuid,
        sql=fixture.sql,
    )
    reference.ducklake_view_uuid = restored.view_uuid
    store.drop_managed(
        name=existing.name,
        expected_uuid=existing.ducklake_table_uuid,
        materialization_id=existing.id,
    )
    session.delete(existing)
    session.flush()
    _create_seeded_materialization(
        session, store, reference, fixture, scope_kind=scope_kind
    )


def _seed_macro(
    session: Session, store: CatalogueTableMacroStore, fixture: RelationFixture
) -> None:
    existing = session.scalar(
        select(CatalogueTableMacroDefinition).where(
            CatalogueTableMacroDefinition.fixture_path == fixture.fixture_path
        )
    )
    if existing is None:
        collision = session.scalar(
            select(CatalogueTableMacroDefinition).where(
                CatalogueTableMacroDefinition.schema_name == TABLE_MACRO_SCHEMA,
                CatalogueTableMacroDefinition.macro_name == fixture.name,
            )
        )
        if collision is not None:
            raise CatalogueFixtureError(
                f"{fixture.fixture_path} conflicts with user-owned table macro "
                f"{TABLE_MACRO_SCHEMA}.{fixture.name}."
            )
        created = create_macro(
            session,
            store,
            slug=fixture.name,
            parameters=list(fixture.parameters),
            parameter_defaults=dict(fixture.parameter_defaults),
            sql=fixture.sql,
            description=None,
        )
        existing = session.get(CatalogueTableMacroDefinition, created.id)
        if existing is None:
            raise RuntimeError("Seeded table macro was not found after creation.")
        existing.fixture_path = fixture.fixture_path
        session.flush()
        return

    if existing.macro_name != fixture.name:
        raise CatalogueFixtureError(
            f"{fixture.fixture_path} changed its macro name from "
            f"{existing.macro_name!r} to {fixture.name!r}; use a new fixture filename."
        )
    if (
        tuple(existing.parameters) != fixture.parameters
        or existing.parameter_defaults != dict(fixture.parameter_defaults)
        or _canonical(existing.sql) != _canonical(fixture.sql)
        or store.get(existing.macro_name) is None
    ):
        update_macro(
            session,
            store,
            existing,
            expected_revision_id=existing.definition_revision_id,
            parameters=list(fixture.parameters),
            parameter_defaults=dict(fixture.parameter_defaults),
            sql=fixture.sql,
            slug=fixture.name,
            description=None,
        )


def _parse_query_fixture(root: Path, path: Path) -> RelationFixture:
    sql = path.read_text(encoding="utf-8").strip()
    try:
        classify_select(sql)
    except CatalogueQueryError as exc:
        raise CatalogueFixtureError(f"Invalid saved query fixture {path}: {exc}") from exc
    return RelationFixture(
        fixture_path=path.relative_to(root).as_posix(),
        name=path.stem,
        sql=sql,
    )


def _parse_relation_fixture(
    root: Path,
    path: Path,
    *,
    kind: str,
    schema: str,
    materialized: bool = False,
) -> RelationFixture:
    source = path.read_text(encoding="utf-8").strip()
    partition_directives = list(_FIXTURE_DAILY_PARTITION.finditer(source))
    if len(partition_directives) > 1:
        raise CatalogueFixtureError(
            f"{path} may declare atlas:partition-by-day at most once."
        )
    if partition_directives and not materialized:
        raise CatalogueFixtureError(
            f"{path} may only declare atlas:partition-by-day as a materialized view."
        )
    partition_column = (
        partition_directives[0].group("column").lower()
        if partition_directives
        else None
    )
    signature_end = (
        re.search(r"\)\s+AS\s+TABLE\b", source, re.IGNORECASE)
        if kind == "MACRO"
        else None
    )
    signature = source[: signature_end.start() + 1] if signature_end else ""
    parameter_defaults = tuple(
        (match.group("name"), match.group("expression"))
        for match in _FIXTURE_PARAMETER_DEFAULT.finditer(signature)
    )
    parse_source = (
        _FIXTURE_PARAMETER_DEFAULT.sub(
            lambda match: match.group("name"),
            signature,
        )
        + source[len(signature) :]
    )
    try:
        statements = [
            statement for statement in parse(parse_source, dialect="duckdb") if statement
        ]
    except ParseError as exc:
        raise CatalogueFixtureError(f"Invalid fixture {path}: {exc}") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Create):
        raise CatalogueFixtureError(
            f"{path} must contain exactly one CREATE {kind} statement."
        )
    statement = statements[0]
    if str(statement.args.get("kind", "")).upper() != kind:
        raise CatalogueFixtureError(f"{path} must contain CREATE {kind}.")

    parameters: tuple[str, ...] = ()
    target = statement.this
    if kind == "MACRO":
        if not isinstance(target, exp.UserDefinedFunction):
            raise CatalogueFixtureError(f"{path} has an invalid table macro signature.")
        if not all(isinstance(item, exp.Identifier) for item in target.expressions):
            raise CatalogueFixtureError(
                f"{path} may only declare positional identifier parameters."
            )
        parameters = tuple(item.name for item in target.expressions)
        target = target.this
        returns = next(statement.find_all(exp.ReturnsProperty), None)
        if returns is None or not returns.args.get("is_table"):
            raise CatalogueFixtureError(f"{path} must declare AS TABLE (...).")
    if not isinstance(target, exp.Table):
        raise CatalogueFixtureError(f"{path} has an invalid object name.")
    actual_schema = target.db or schema
    if actual_schema != schema:
        raise CatalogueFixtureError(
            f"{path} must create its object in the {schema} schema."
        )
    name = target.name
    if name != path.stem:
        raise CatalogueFixtureError(
            f"{path} must declare {schema}.{path.stem}, not {schema}.{name}."
        )
    expression = statement.expression
    if isinstance(expression, exp.Subquery):
        expression = expression.this
    if not isinstance(expression, exp.Query):
        raise CatalogueFixtureError(f"{path} must contain a SELECT query body.")
    sql = expression.sql(dialect="duckdb", pretty=True)
    classify_select(sql)
    return RelationFixture(
        fixture_path=path.relative_to(root).as_posix(),
        name=name,
        sql=sql,
        parameters=parameters,
        parameter_defaults=parameter_defaults,
        partition_column=partition_column,
    )


def _sql_files(directory: Path) -> list[Path]:
    return sorted(path for path in directory.glob("*.sql") if path.is_file())


def _ordered_table_macro_paths(paths: list[Path]) -> list[Path]:
    """Install fixture macros after the fixture macros they reference."""

    by_name = {path.stem: path for path in paths}
    dependencies = {
        name: (
            {
                reference.lower()
                for reference in re.findall(
                    r"\bmacros\.([a-z_][a-z0-9_]*)\s*\(",
                    path.read_text(encoding="utf-8"),
                    re.IGNORECASE,
                )
            }
            & set(by_name)
        )
        - {name}
        for name, path in by_name.items()
    }
    remaining = dict(by_name)
    ordered: list[Path] = []
    installed: set[str] = set()
    while remaining:
        ready = sorted(
            name
            for name in remaining
            if dependencies.get(name, set()).issubset(installed)
        )
        if not ready:
            unresolved = ", ".join(sorted(remaining))
            raise CatalogueFixtureError(
                f"Table macro fixtures have unresolved dependencies: {unresolved}."
            )
        for name in ready:
            ordered.append(remaining.pop(name))
            installed.add(name)
    return ordered


def _canonical(sql: str) -> str:
    return classify_select(sql).sql(dialect="duckdb")


def _canonical_expression(sql: str) -> str:
    return parse(f"SELECT ({sql})", dialect="duckdb")[0].sql(dialect="duckdb")
