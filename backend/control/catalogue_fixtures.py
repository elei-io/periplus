"""Idempotent SQL fixture seeding for user-visible catalogue definitions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlglot import exp, parse
from sqlglot.errors import ParseError

from control.catalogue_queries.models import CatalogueQuery
from control.catalogue_queries.service import create_query, detail, restore_query, update_query
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
from repository.catalogue.query import CatalogueQueryError, classify_select
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


def seed_catalogue_fixtures(
    session: Session, catalogue: Catalogue, fixtures_root: Path
) -> None:
    """Seed every SQL fixture without overwriting a user-owned name collision."""

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
    macro_paths = _sql_files(fixtures_root / "macros")
    for path in macro_paths:
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
        or _canonical(existing.sql) != _canonical(fixture.sql)
        or store.get(existing.macro_name) is None
    ):
        update_macro(
            session,
            store,
            existing,
            expected_revision_id=existing.definition_revision_id,
            parameters=list(fixture.parameters),
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
    root: Path, path: Path, *, kind: str, schema: str
) -> RelationFixture:
    source = path.read_text(encoding="utf-8").strip()
    try:
        statements = [statement for statement in parse(source, dialect="duckdb") if statement]
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
    )


def _sql_files(directory: Path) -> list[Path]:
    return sorted(path for path in directory.glob("*.sql") if path.is_file())


def _canonical(sql: str) -> str:
    return classify_select(sql).sql(dialect="duckdb")
