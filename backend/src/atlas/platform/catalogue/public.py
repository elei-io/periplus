"""Install and validate the portable public DuckLake catalogue."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from typing import Literal, Protocol

from atlas.platform.catalogue.exceptions import CatalogueSchemaError


PUBLIC_CATALOGUE_VERSION = "3.0.0"
WEB_SCHEMA = "web"
DOM_SCHEMA = "dom"
PUBLIC_SCHEMAS = (WEB_SCHEMA, DOM_SCHEMA)


class CatalogueConnection(Protocol):
    def remote_transaction(self): ...

    def trusted_remote_execute(self, sql: str) -> list[tuple]: ...

    def trusted_remote_rows(self, sql: str) -> list[tuple]: ...


@dataclass(frozen=True, slots=True)
class CatalogueObject:
    kind: Literal["macro", "view", "table_macro"]
    name: str
    resource: str
    columns: tuple[str, ...] = ()
    arguments_sql: str | None = None
    parameters: tuple[tuple[str, str], ...] = ()
    return_type: str | None = None
    exposed: bool = True
    schema: Literal["web", "dom"] = WEB_SCHEMA
    comment: str | None = None
    column_comments: tuple[tuple[str, str], ...] = ()


PUBLIC_OBJECTS = (
    CatalogueObject(
        "macro",
        "_catalogue_version",
        "macros_scalar/000_catalogue_version.sql",
        return_type="VARCHAR",
        exposed=False,
    ),
    CatalogueObject(
        "macro",
        "get_attribute",
        "macros_scalar/010_get_attribute.sql",
        parameters=(
            ("element_attributes", "MAP(VARCHAR, VARCHAR)"),
            ("attribute_name", "VARCHAR"),
        ),
        return_type="VARCHAR",
        schema=DOM_SCHEMA,
    ),
    CatalogueObject(
        "view",
        "page",
        "views/001_page.sql",
        (
            "page_id",
            "url",
            "scheme",
            "hostname",
            "port",
            "path",
            "query",
            "registrable_domain",
        ),
        comment="Canonical normalized URL identities observed through visits.",
        column_comments=(
            ("page_id", "Deterministic identity derived from the normalized URL."),
            ("url", "Unique normalized URL represented by this page."),
            ("scheme", "Normalized URL scheme."),
            ("hostname", "Normalized hostname."),
            ("port", "Explicit non-default port, otherwise null."),
            ("path", "Normalized URL path."),
            ("query", "Preserved query string, null when absent."),
            (
                "registrable_domain",
                "Public-suffix-aware domain when derivable.",
            ),
        ),
    ),
    CatalogueObject(
        "view",
        "visit",
        "views/010_visit.sql",
        (
            "visit_id",
            "page_id",
            "url",
            "is_latest",
            "crawl_id",
            "requested_url",
            "effective_url",
            "admitted_at",
            "started_at",
            "observed_at",
            "finished_at",
            "outcome",
            "status_code",
            "document_id",
            "content_id",
            "content_bytes",
            "representation",
            "declared_media_type",
            "detected_media_type",
            "charset",
            "dom_projection_complete",
            "dom_element_count",
            "dom_max_depth",
            "provenance",
        ),
        comment="Acquisition history with page, document, and DOM evidence.",
        column_comments=(
            ("visit_id", "Unique identity of this acquisition visit."),
            ("page_id", "Canonical effective page identity for this visit."),
            ("url", "Normalized effective page URL for this visit."),
            ("is_latest", "Whether this is the deterministically latest page visit."),
            ("crawl_id", "Crawl execution that produced this visit."),
            ("requested_url", "Exact URL Atlas attempted to visit."),
            ("effective_url", "Final URL after navigation or redirects."),
            ("admitted_at", "Time the destination entered the crawl."),
            ("started_at", "Time acquisition began."),
            ("observed_at", "Time the retained representation was captured."),
            ("finished_at", "Time the visit reached its terminal outcome."),
            ("outcome", "Final logical visit result."),
            ("status_code", "Final HTTP status when available."),
            ("document_id", "Retained document observation, when one exists."),
            ("content_id", "Immutable logical content identity."),
            ("content_bytes", "Size of the uncompressed logical content."),
            ("representation", "Meaning of the retained bytes."),
            ("declared_media_type", "Media type claimed by the source."),
            ("detected_media_type", "Media type detected by Atlas."),
            ("charset", "Character encoding when meaningful."),
            (
                "dom_projection_complete",
                "Whether the content has a committed DOM projection.",
            ),
            ("dom_element_count", "Projected DOM element count."),
            ("dom_max_depth", "Maximum projected DOM element depth."),
            ("provenance", "Typed origin of this visit evidence."),
        ),
    ),
    CatalogueObject(
        "view",
        "link",
        "views/002_link.sql",
        (
            "link_id",
            "source_page_id",
            "target_page_id",
            "source_url",
            "target_url",
            "relation_scope",
            "first_seen_at",
            "last_seen_at",
            "visit_count",
            "distinct_content_count",
            "occurrence_count",
        ),
        comment="Canonical directed page links with retained history rollups.",
        column_comments=(
            ("link_id", "Deterministic identity of the directed page pair."),
            ("source_page_id", "Canonical normalized source page identity."),
            ("target_page_id", "Canonical normalized target page identity."),
            ("source_url", "Normalized source URL."),
            ("target_url", "Normalized resolved target URL."),
            ("relation_scope", "Most-specific source-target site relationship."),
            ("first_seen_at", "Earliest retained occurrence time."),
            ("last_seen_at", "Latest retained occurrence time."),
            ("visit_count", "Visits containing this link."),
            (
                "distinct_content_count",
                "Distinct content identities containing this link.",
            ),
            ("occurrence_count", "Retained DOM occurrences of this link."),
        ),
    ),
    CatalogueObject(
        "view",
        "link_occurrence",
        "views/011_link_occurrence.sql",
        (
            "occurrence_id",
            "link_id",
            "visit_id",
            "source_page_id",
            "target_page_id",
            "document_id",
            "content_id",
            "element_index",
            "observed_at",
            "raw_href",
            "resolved_url",
            "relation_scope",
        ),
        comment="Exact DOM occurrences supporting canonical page links.",
        column_comments=(
            ("occurrence_id", "Stable identity of this link occurrence."),
            ("link_id", "Canonical directed link supported by this occurrence."),
            ("visit_id", "Visit during which this occurrence was retained."),
            ("source_page_id", "Canonical source page identity."),
            ("target_page_id", "Canonical resolved target page identity."),
            ("document_id", "Document observation containing the element."),
            ("content_id", "Immutable content containing the element."),
            ("element_index", "Exact source element in dom.elements."),
            ("observed_at", "Time the containing representation was captured."),
            ("raw_href", "Exact href before resolution and normalization."),
            ("resolved_url", "Normalized target URL resolved in visit context."),
            ("relation_scope", "Most-specific source-target site relationship."),
        ),
    ),
    CatalogueObject(
        "view",
        "crawls",
        "views/014_crawls.sql",
        (
            "crawl_id",
            "kind",
            "graph_id",
            "graph_config_hash",
            "graph_config",
            "root_url_count",
            "started_at",
            "finished_at",
            "stop_reason",
        ),
        comment="Terminal crawl execution evidence.",
        column_comments=(
            ("crawl_id", "Unique identity of this crawl execution."),
            ("kind", "Native Atlas crawl or imported evidence."),
            ("graph_id", "Stable crawl-plan identity for native crawls."),
            ("graph_config_hash", "Hash of the frozen graph configuration."),
            ("graph_config", "Complete frozen graph configuration."),
            ("root_url_count", "Number of admitted crawl roots."),
            ("started_at", "Time crawl execution began."),
            ("finished_at", "Time crawl execution stopped."),
            ("stop_reason", "Reason crawl execution stopped."),
        ),
    ),
    CatalogueObject(
        "view",
        "jsonld",
        "views/021_jsonld.sql",
        ("content_id", "element_index", "type_terms", "value"),
        comment="Parsed JSON-LD values embedded in immutable HTML content.",
        column_comments=(
            ("content_id", "Immutable HTML content identity."),
            ("element_index", "Source script element in dom.elements."),
            ("type_terms", "Distinct raw JSON-LD @type terms."),
            ("value", "Complete parsed JSON-LD value."),
        ),
    ),
    CatalogueObject(
        "view",
        "elements",
        "views/001_elements.sql",
        (
            "content_id",
            "element_index",
            "parent_index",
            "subtree_end_index",
            "depth",
            "child_index",
            "tag",
            "namespace",
            "attributes",
            "text_direct",
            "text_tail",
        ),
        schema=DOM_SCHEMA,
        comment="Structural DOM elements keyed by immutable content.",
        column_comments=(
            ("content_id", "Immutable HTML content identity."),
            ("element_index", "Zero-based depth-first document position."),
            ("parent_index", "Parent element index, null for the root."),
            ("subtree_end_index", "Exclusive end of this element subtree."),
            ("depth", "Element depth from the document root."),
            ("child_index", "Zero-based position among element siblings."),
            ("tag", "Normalized local tag name."),
            ("namespace", "Normalized element namespace."),
            ("attributes", "Attribute names and string values."),
            ("text_direct", "Text directly inside this element."),
            ("text_tail", "Text following this element within its parent."),
        ),
    ),
    CatalogueObject(
        "table_macro",
        "text_content",
        "macros_table/100_text_content.sql",
        ("content_id", "element_index", "text_content"),
        arguments_sql="NULL::VARCHAR, NULL::INTEGER",
        parameters=(
            ("selected_content_id", "VARCHAR"),
            ("selected_element_index", "INTEGER"),
        ),
        schema=DOM_SCHEMA,
    ),
)

_DOM_ELEMENT_COLUMNS = (
    "content_id",
    "element_index",
    "parent_index",
    "subtree_end_index",
    "depth",
    "child_index",
    "tag",
    "namespace",
    "attributes",
    "text_direct",
    "text_tail",
)
DOM_SELECTOR_OBJECTS = (
    CatalogueObject(
        "table_macro",
        "query_selector",
        "macros_table/110_query_selector.sql",
        _DOM_ELEMENT_COLUMNS,
        arguments_sql="NULL::VARCHAR, 'a'::VARCHAR",
        parameters=(
            ("selected_content_id", "VARCHAR"),
            ("css_selector", "VARCHAR"),
        ),
        schema=DOM_SCHEMA,
    ),
    CatalogueObject(
        "table_macro",
        "query_selector_all",
        "macros_table/120_query_selector_all.sql",
        _DOM_ELEMENT_COLUMNS,
        arguments_sql="NULL::VARCHAR, 'a'::VARCHAR",
        parameters=(
            ("selected_content_id", "VARCHAR"),
            ("css_selector", "VARCHAR"),
        ),
        schema=DOM_SCHEMA,
    ),
)
KNOWN_PUBLIC_OBJECTS = (*PUBLIC_OBJECTS, *DOM_SELECTOR_OBJECTS)
_DOM_SELECTOR_NATIVE_FUNCTIONS = frozenset(
    {"atlas_dom_select_first", "atlas_dom_select_all"}
)


def dom_selector_extension_available(
    catalogue: CatalogueConnection,
) -> bool:
    names = {
        str(name)
        for (name,) in catalogue.trusted_remote_rows(
            "SELECT DISTINCT function_name FROM duckdb_functions() "
            "WHERE function_name IN "
            "('atlas_dom_select_first', 'atlas_dom_select_all')"
        )
    }
    return names == _DOM_SELECTOR_NATIVE_FUNCTIONS


def installed_public_objects(
    catalogue: CatalogueConnection,
) -> tuple[CatalogueObject, ...]:
    if not dom_selector_extension_available(catalogue):
        return PUBLIC_OBJECTS
    installed = {
        (str(schema), str(name))
        for schema, name in catalogue.trusted_remote_rows(
            "SELECT schema_name, function_name FROM duckdb_functions() "
            "WHERE function_type = 'table_macro' "
            "AND schema_name = 'dom' "
            "AND function_name IN ('query_selector', 'query_selector_all')"
        )
    }
    if installed == {
        ("dom", "query_selector"),
        ("dom", "query_selector_all"),
    }:
        return KNOWN_PUBLIC_OBJECTS
    return PUBLIC_OBJECTS


def install_public_catalogue(catalogue: CatalogueConnection) -> None:
    """Atomically replace the complete current public SQL contract."""

    extension_available = dom_selector_extension_available(catalogue)
    install_objects = (
        KNOWN_PUBLIC_OBJECTS if extension_available else PUBLIC_OBJECTS
    )
    for item in PUBLIC_OBJECTS:
        if item.kind == "view":
            _validated_view_comments(item)

    root = files("atlas.platform.catalogue").joinpath("sql")
    with catalogue.remote_transaction():
        for schema in PUBLIC_SCHEMAS:
            catalogue.trusted_remote_execute(
                f"CREATE SCHEMA IF NOT EXISTS {schema}"
            )
            _drop_superseded_public_objects(catalogue, schema)
        if not extension_available:
            for item in DOM_SELECTOR_OBJECTS:
                catalogue.trusted_remote_execute(
                    f"DROP MACRO IF EXISTS {item.schema}."
                    f"{_quote_identifier(item.name)}"
                )
        for item in install_objects:
            sql = root.joinpath(
                item.schema, item.resource
            ).read_text(encoding="utf-8")
            catalogue.trusted_remote_execute(sql)
            if item.kind == "view":
                _install_view_comments(catalogue, item)


def _install_view_comments(
    catalogue: CatalogueConnection,
    item: CatalogueObject,
) -> None:
    comment, _column_comments = _validated_view_comments(item)
    qualified = (
        f"{_quote_identifier(item.schema)}.{_quote_identifier(item.name)}"
    )
    catalogue.trusted_remote_execute(
        f"COMMENT ON VIEW {qualified} IS {_quote_literal(comment)}"
    )


def _validated_view_comments(
    item: CatalogueObject,
) -> tuple[str, dict[str, str]]:
    qualified = f"{item.schema}.{item.name}"
    if item.comment is None:
        raise ValueError(f"{qualified} has no view comment")
    column_comments = dict(item.column_comments)
    comment_columns = tuple(column_comments)
    if len(column_comments) != len(item.column_comments):
        raise ValueError(f"{qualified} has duplicate column comments")
    if comment_columns != item.columns:
        raise ValueError(
            f"{qualified} comment columns: expected {item.columns}, "
            f"got {comment_columns}"
        )
    return item.comment, column_comments


def _drop_superseded_public_objects(
    catalogue: CatalogueConnection,
    schema: str,
) -> None:
    expected_views = {
        item.name
        for item in KNOWN_PUBLIC_OBJECTS
        if item.schema == schema and item.kind == "view"
    }
    existing_views = {
        str(row[0])
        for row in catalogue.trusted_remote_rows(
            "SELECT view_name FROM duckdb_views() "
            f"WHERE schema_name = '{schema}'"
        )
    }
    for name in sorted(existing_views - expected_views):
        catalogue.trusted_remote_execute(
            f"DROP VIEW {schema}.{_quote_identifier(name)}"
        )

    expected_macros = {
        (item.name, item.kind)
        for item in KNOWN_PUBLIC_OBJECTS
        if item.schema == schema
        and item.kind in {"macro", "table_macro"}
    }
    existing_macros = {
        (str(row[0]), str(row[1]))
        for row in catalogue.trusted_remote_rows(
            "SELECT function_name, function_type FROM duckdb_functions() "
            f"WHERE schema_name = '{schema}' "
            "AND function_type IN ('macro', 'table_macro')"
        )
    }
    for name, _kind in sorted(existing_macros - expected_macros):
        catalogue.trusted_remote_execute(
            f"DROP MACRO {schema}.{_quote_identifier(name)}"
        )


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def validate_public_catalogue(catalogue: CatalogueConnection) -> None:
    """Fail when the installed public catalogue differs from the manifest."""

    errors: list[str] = []
    extension_available = dom_selector_extension_available(catalogue)
    for schema in PUBLIC_SCHEMAS:
        expected_views = {
            item.name
            for item in PUBLIC_OBJECTS
            if item.schema == schema and item.kind == "view"
        }
        actual_views = {
            str(row[0])
            for row in catalogue.trusted_remote_rows(
                "SELECT view_name FROM duckdb_views() "
                f"WHERE schema_name = '{schema}'"
            )
        }
        if actual_views != expected_views:
            errors.append(
                f"{schema} views: expected {sorted(expected_views)}, "
                f"got {sorted(actual_views)}"
            )

        required_macros = {
            (item.name, item.kind)
            for item in PUBLIC_OBJECTS
            if item.schema == schema
            and item.kind in {"macro", "table_macro"}
        }
        actual_macros = {
            (str(row[0]), str(row[1]))
            for row in catalogue.trusted_remote_rows(
                "SELECT function_name, function_type "
                "FROM duckdb_functions() "
                f"WHERE schema_name = '{schema}' "
                "AND function_type IN ('macro', 'table_macro')"
            )
        }
        known_macros = {
            (item.name, item.kind)
            for item in KNOWN_PUBLIC_OBJECTS
            if item.schema == schema
            and item.kind in {"macro", "table_macro"}
        }
        selector_macros = {
            (item.name, item.kind)
            for item in DOM_SELECTOR_OBJECTS
            if item.schema == schema
        }
        expected_macros = (
            required_macros | selector_macros
            if extension_available
            else required_macros
        )
        optional_installed = actual_macros & selector_macros
        if (
            not required_macros.issubset(actual_macros)
            or not actual_macros.issubset(known_macros)
            or (
                not extension_available
                and optional_installed
            )
            or (
                extension_available
                and optional_installed != selector_macros
            )
        ):
            errors.append(
                f"{schema} macros: expected {sorted(expected_macros)}, "
                f"got {sorted(actual_macros)}"
            )

    validate_objects = (
        KNOWN_PUBLIC_OBJECTS if extension_available else PUBLIC_OBJECTS
    )
    for item in validate_objects:
        qualified = f"{item.schema}.{item.name}"
        if not item.columns:
            continue
        try:
            if item.kind == "view":
                rows = catalogue.trusted_remote_rows(
                    f"DESCRIBE {qualified}"
                )
            else:
                if item.arguments_sql is None:
                    raise ValueError(
                        f"{qualified} has no validation arguments"
                    )
                rows = catalogue.trusted_remote_rows(
                    f"DESCRIBE SELECT * FROM {qualified}"
                    f"({item.arguments_sql})"
                )
        except Exception as exc:
            errors.append(f"{qualified}: {exc}")
            continue
        actual_columns = tuple(str(row[0]) for row in rows)
        if actual_columns != item.columns:
            errors.append(
                f"{qualified}: expected columns {item.columns}, "
                f"got {actual_columns}"
            )
        if item.kind == "view":
            _validate_view_comments(catalogue, item, errors)

    try:
        rows = catalogue.trusted_remote_rows(
            f"SELECT {WEB_SCHEMA}._catalogue_version()"
        )
        actual_version = str(rows[0][0])
    except Exception as exc:
        errors.append(f"web catalogue version: {exc}")
    else:
        if actual_version != PUBLIC_CATALOGUE_VERSION:
            errors.append(
                "public catalogue version: expected "
                f"{PUBLIC_CATALOGUE_VERSION}, "
                f"got {actual_version}"
            )

    if errors:
        raise CatalogueSchemaError("; ".join(errors))


def _validate_view_comments(
    catalogue: CatalogueConnection,
    item: CatalogueObject,
    errors: list[str],
) -> None:
    qualified = f"{item.schema}.{item.name}"
    try:
        expected_comment, _column_comments = _validated_view_comments(item)
        view_rows = catalogue.trusted_remote_rows(
            "SELECT comment FROM duckdb_views() "
            f"WHERE schema_name = {_quote_literal(item.schema)} "
            f"AND view_name = {_quote_literal(item.name)}"
        )
    except Exception as exc:
        errors.append(f"{qualified} comments: {exc}")
        return

    actual_comment = view_rows[0][0] if len(view_rows) == 1 else None
    if actual_comment != expected_comment:
        errors.append(f"{qualified}: missing or stale view comment")
