"""Idempotent SQL fixture seeding for user-visible catalogue definitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
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
    materialization_store,
    put_for_view as materialize_view,
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
from repository.catalogue.materializations import MaterializationStore
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
    description: str | None = None
    parameters: tuple[str, ...] = ()
    parameter_defaults: tuple[tuple[str, str], ...] = ()
    partition_column: str | None = None
    refresh_strategy: str | None = None
    key_columns: tuple[str, ...] = ()


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
_FIXTURE_REFRESH = re.compile(
    r"^\s*--\s*atlas:refresh\s*=\s*"
    r"(?P<strategy>keyed|append|full)"
    r"(?:\((?P<columns>[a-z_][a-z0-9_]*(?:\s*,\s*[a-z_][a-z0-9_]*)*)\))?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_FIXTURE_METADATA = re.compile(
    r"^\s*--\s*atlas:(?P<name>[a-z][a-z0-9-]*)\s*=\s*(?P<value>.*?)\s*$",
    re.IGNORECASE,
)
_FIXTURE_METADATA_FIELDS = frozenset({"description"})


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
    for driver_kind in ("document", "crawl"):
        for path in _sql_files(fixtures_root / "materialized_views" / driver_kind):
            fixture = _parse_relation_fixture(
                fixtures_root,
                path,
                kind="VIEW",
                schema=VIEW_SCHEMA,
                materialized=True,
            )
            _seed_view(session, view_store, fixture)
            materialized_fixtures.append((driver_kind, fixture))
    for driver_kind, fixture in materialized_fixtures:
        _seed_materialization(
            session,
            catalogue,
            fixture,
            driver_kind=driver_kind,
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
        metadata = _parse_fixture_metadata(path, source)
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
                description=metadata.get("description"),
            )
            existing = session.get(CatalogueScalarMacroDefinition, created.id)
            if existing is None:
                raise RuntimeError("Seeded scalar macro was not found after creation.")
            existing.fixture_path = fixture_path
            session.flush()
        elif (
            existing.parameters != parameters
            or _canonical_expression(existing.sql) != _canonical_expression(sql)
            or existing.description != metadata.get("description")
            or store.get(existing.macro_name) is None
        ):
            macro = store.replace(
                name=existing.macro_name,
                parameters=parameters,
                sql=sql,
            )
            existing.parameters = list(macro.parameters)
            existing.sql = sql
            existing.description = metadata.get("description")
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
            description=fixture.description,
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
    if (
        _canonical(current.sql) != _canonical(fixture.sql)
        or existing.description != fixture.description
    ):
        update_query(
            session,
            existing,
            expected_revision_id=current.current_revision_id,
            sql=fixture.sql,
            slug=fixture.name,
            description=fixture.description,
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
            description=fixture.description,
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
    materialization = session.scalar(
        select(CatalogueMaterialization).where(
            CatalogueMaterialization.view_reference_id == existing.id,
            CatalogueMaterialization.archived_at.is_(None),
        )
    )
    if materialization is not None:
        _require_current_fixture_materialization(materialization, fixture)
    physical = store.definition_for_uuid(existing.ducklake_view_uuid)
    if physical is None:
        if materialization is not None:
            # The selected managed lake is replaceable deployment state. If
            # its fixture-owned physical relation is absent, retire the stale
            # incarnation and let this setup pass recreate it from the
            # authoritative fixture definition.
            materialization.archived_at = datetime.now(UTC)
            materialization.last_error = (
                "Retired because the selected DuckBasin lake did not contain "
                "this fixture-owned materialization."
            )
        physical = store.definition_for_name(fixture.name)
        if physical is None:
            physical = store.create(
                name=fixture.name,
                sql=fixture.sql,
                description=fixture.description,
            )
        elif _canonical(physical.sql) != _canonical(fixture.sql):
            physical = store.replace(
                current_uuid=physical.view_uuid,
                sql=fixture.sql,
                description=fixture.description,
            )
        existing.ducklake_view_uuid = physical.view_uuid
        existing.schema_name = physical.schema_name
        existing.view_name = physical.view_name
        session.flush()
    elif (
        materialization is None
        and _canonical(physical.sql) != _canonical(fixture.sql)
    ):
        physical = store.replace(
            current_uuid=physical.view_uuid,
            sql=fixture.sql,
            description=fixture.description,
        )
        existing.ducklake_view_uuid = physical.view_uuid
        existing.schema_name = physical.schema_name
        existing.view_name = physical.view_name
        session.flush()
    current = get_view_record(session, store, existing.id)
    if current is None or not current.available:
        raise CatalogueFixtureError(
            f"Fixture-owned view {VIEW_SCHEMA}.{fixture.name} "
            "could not be restored in DuckLake."
        )
    if (
        _canonical(current.sql) != _canonical(fixture.sql)
        or current.description != fixture.description
    ):
        update_view(
            session,
            store,
            existing,
            expected_uuid=current.ducklake_view_uuid,
            sql=fixture.sql,
            slug=fixture.name,
            description=fixture.description,
        )


def _seed_materialization(
    session: Session,
    catalogue: Catalogue,
    fixture: RelationFixture,
    *,
    driver_kind: str,
) -> None:
    reference = session.scalar(
        select(CatalogueViewReference).where(
            CatalogueViewReference.fixture_path == fixture.fixture_path
        )
    )
    if reference is None:
        raise RuntimeError("Seeded materialized view reference was not found.")
    source_table = {
        "document": "documents",
        "crawl": "crawls",
    }[driver_kind]
    existing = session.scalar(
        select(CatalogueMaterialization).where(
            CatalogueMaterialization.view_reference_id == reference.id,
            CatalogueMaterialization.archived_at.is_(None),
        )
    )
    store = materialization_store(session, catalogue)
    if existing is None:
        _create_seeded_materialization(
            session, store, reference, fixture, driver_kind=driver_kind
        )
        return
    if existing.source_table != source_table:
        raise CatalogueFixtureError(
            f"{fixture.fixture_path} changed its driving table from "
            f"{existing.source_table} to {source_table}; reset fixture state."
        )
    if existing.partition_column != fixture.partition_column:
        raise CatalogueFixtureError(
            f"{fixture.fixture_path} changed physical partitioning; reset fixture state."
        )
    if (
        existing.refresh_strategy != fixture.refresh_strategy
        or tuple(existing.key_columns) != fixture.key_columns
    ):
        raise CatalogueFixtureError(
            f"{fixture.fixture_path} changed its refresh strategy; reset fixture state."
        )
    if existing.description != fixture.description:
        existing.description = fixture.description
        session.flush()
    _require_current_fixture_materialization(existing, fixture)


def _require_current_fixture_materialization(
    materialization: CatalogueMaterialization,
    fixture: RelationFixture,
) -> None:
    """Compare fixture-authored SQL only with its persisted authored snapshot."""

    if materialization.fixture_source_sql is None:
        raise CatalogueFixtureError(
            f"{fixture.fixture_path} materialization has no authoritative fixture "
            "definition; reset fixture state."
        )
    if _canonical(materialization.fixture_source_sql) != _canonical(fixture.sql):
        raise CatalogueFixtureError(
            f"{fixture.fixture_path} changed its materialized SQL definition; "
            "reset fixture state."
        )


def _create_seeded_materialization(
    session: Session,
    store: MaterializationStore,
    reference: CatalogueViewReference,
    fixture: RelationFixture,
    *,
    driver_kind: str,
) -> None:
    if fixture.refresh_strategy is None:
        raise RuntimeError("Materialized fixture refresh strategy is missing.")
    created = materialize_view(
        session,
        store,
        view_reference_id=reference.id,
        name=fixture.name,
        display_name=None,
        description=fixture.description,
        source_table={
            "document": "documents",
            "crawl": "crawls",
        }[driver_kind],
        refresh_strategy=fixture.refresh_strategy,
        key_columns=list(fixture.key_columns),
        refresh_delay_seconds=1,
        partition_column=fixture.partition_column,
    )
    model = session.get(CatalogueMaterialization, created.id)
    if model is None:
        raise RuntimeError("Seeded materialization was not found after creation.")
    # DuckLake returns a catalogue-qualified form of a newly created view.
    # The fixture itself remains the authoritative, portable source query.
    model.source_sql = fixture.sql
    model.fixture_source_sql = fixture.sql
    session.flush()

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
            description=fixture.description,
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
        or existing.description != fixture.description
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
            description=fixture.description,
        )


def _parse_query_fixture(root: Path, path: Path) -> RelationFixture:
    sql = path.read_text(encoding="utf-8").strip()
    metadata = _parse_fixture_metadata(path, sql)
    try:
        classify_select(sql)
    except CatalogueQueryError as exc:
        raise CatalogueFixtureError(f"Invalid saved query fixture {path}: {exc}") from exc
    return RelationFixture(
        fixture_path=path.relative_to(root).as_posix(),
        name=path.stem,
        sql=sql,
        description=metadata.get("description"),
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
    metadata = _parse_fixture_metadata(path, source)
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
    refresh_directives = list(_FIXTURE_REFRESH.finditer(source))
    if len(refresh_directives) > 1:
        raise CatalogueFixtureError(
            f"{path} may declare atlas:refresh at most once."
        )
    if refresh_directives and not materialized:
        raise CatalogueFixtureError(
            f"{path} may only declare atlas:refresh as a materialized view."
        )
    if materialized and not refresh_directives:
        raise CatalogueFixtureError(
            f"{path} must declare atlas:refresh."
        )
    refresh_strategy: str | None = None
    key_columns: tuple[str, ...] = ()
    if refresh_directives:
        directive = refresh_directives[0]
        refresh_strategy = directive.group("strategy").lower()
        columns = directive.group("columns")
        key_columns = (
            tuple(column.strip().lower() for column in columns.split(","))
            if columns
            else ()
        )
        if refresh_strategy in {"keyed", "append"} and not key_columns:
            raise CatalogueFixtureError(
                f"{path} {refresh_strategy} refresh requires key columns."
            )
        if refresh_strategy == "full" and key_columns:
            raise CatalogueFixtureError(
                f"{path} full refresh does not accept key columns."
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
        description=metadata.get("description"),
        parameters=parameters,
        parameter_defaults=parameter_defaults,
        partition_column=partition_column,
        refresh_strategy=refresh_strategy,
        key_columns=key_columns,
    )


def _parse_fixture_metadata(path: Path, source: str) -> dict[str, str]:
    """Parse Atlas metadata from the leading SQL comment block."""

    metadata: dict[str, str] = {}
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped:
            if metadata:
                continue
            break
        if not stripped.startswith("--"):
            break
        match = _FIXTURE_METADATA.fullmatch(line)
        if match is None:
            continue
        name = match.group("name").lower()
        if name not in _FIXTURE_METADATA_FIELDS:
            continue
        if name in metadata:
            raise CatalogueFixtureError(
                f"{path} may declare atlas:{name} at most once."
            )
        value = match.group("value").strip()
        if not value:
            raise CatalogueFixtureError(
                f"{path} atlas:{name} must not be empty."
            )
        if len(value) > 2_000:
            raise CatalogueFixtureError(
                f"{path} atlas:{name} exceeds 2,000 characters."
            )
        metadata[name] = value
    return metadata


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
