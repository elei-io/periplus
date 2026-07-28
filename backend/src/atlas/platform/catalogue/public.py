"""Install and validate the portable public DuckLake catalogue."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from typing import Literal, Protocol

from atlas.platform.catalogue.exceptions import CatalogueSchemaError


PUBLIC_CATALOGUE_VERSION = "2.1.0"
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
        "pages",
        "views/001_pages.sql",
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
        comment="Normalized page identities observed through visits.",
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
        "links",
        "views/002_links.sql",
        (
            "link_id",
            "source_page_id",
            "target_page_id",
            "source_url",
            "target_url",
            "relation_scope",
        ),
        comment=(
            "Normalized directed page pairs positively observed in HTML."
        ),
        column_comments=(
            (
                "link_id",
                "Deterministic identity of the directed normalized page pair.",
            ),
            (
                "source_page_id",
                "Deterministic identity of the normalized source URL.",
            ),
            (
                "target_page_id",
                "Deterministic identity of the normalized target URL.",
            ),
            (
                "source_url",
                "Normalized fragment-free URL where the link was observed.",
            ),
            (
                "target_url",
                "Normalized fragment-free URL resolved from the observed href.",
            ),
            (
                "relation_scope",
                "Most-specific deterministic source-target relationship.",
            ),
        ),
    ),
    CatalogueObject(
        "view",
        "content",
        "views/003_content.sql",
        ("content_id", "content_bytes"),
        comment="Unique captured logical byte payload identities.",
        column_comments=(
            (
                "content_id",
                "SHA-256 identity of the uncompressed logical bytes.",
            ),
            (
                "content_bytes",
                "Size of the uncompressed logical bytes.",
            ),
        ),
    ),
    CatalogueObject(
        "view",
        "page_observations",
        "views/010_page_observations.sql",
        ("page_id", "visit_id", "document_id", "observed_at"),
        comment="Page observations made by individual visits.",
        column_comments=(
            ("page_id", "Normalized page identity observed by the visit."),
            ("visit_id", "Visit that made this page observation."),
            (
                "document_id",
                "Retained document observation, null when none was produced.",
            ),
            (
                "observed_at",
                "Time the page representation was captured.",
            ),
        ),
    ),
    CatalogueObject(
        "view",
        "link_observations",
        "views/011_link_observations.sql",
        (
            "link_id",
            "document_id",
            "content_id",
            "element_index",
            "raw_href",
            "observed_at",
        ),
        comment="Anchor occurrences observed in retained HTML documents.",
        column_comments=(
            ("link_id", "Directed normalized page-pair identity."),
            (
                "document_id",
                "Document observation containing the anchor.",
            ),
            (
                "content_id",
                "Immutable HTML content containing the anchor.",
            ),
            (
                "element_index",
                "Exact source anchor position in dom.elements.",
            ),
            (
                "raw_href",
                "Href value before URL resolution and normalization.",
            ),
            (
                "observed_at",
                "Time the document representation was captured.",
            ),
        ),
    ),
    CatalogueObject(
        "view",
        "documents",
        "views/012_documents.sql",
        (
            "document_id",
            "visit_id",
            "page_id",
            "content_id",
            "observed_at",
            "representation",
            "declared_media_type",
            "detected_media_type",
            "charset",
            "content_bytes",
        ),
        comment="Retained document representation observations.",
        column_comments=(
            ("document_id", "Unique identity of this document observation."),
            ("visit_id", "Visit that produced this document."),
            (
                "page_id",
                "Normalized effective page identity observed by the visit.",
            ),
            (
                "content_id",
                "Identity of the immutable logical bytes.",
            ),
            (
                "observed_at",
                "Time the document representation was captured.",
            ),
            (
                "representation",
                "Meaning of the retained representation.",
            ),
            (
                "declared_media_type",
                "Media type claimed by the source, null when unavailable.",
            ),
            (
                "detected_media_type",
                "Media type detected by Atlas.",
            ),
            (
                "charset",
                "Character encoding when meaningful, otherwise null.",
            ),
            (
                "content_bytes",
                "Size of the uncompressed logical bytes.",
            ),
        ),
    ),
    CatalogueObject(
        "view",
        "visits",
        "views/013_visits.sql",
        (
            "visit_id",
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
            "provenance",
        ),
        comment="Destinations admitted and observed during crawls.",
        column_comments=(
            ("visit_id", "Unique identity of this destination observation."),
            ("crawl_id", "Crawl that produced this visit."),
            (
                "requested_url",
                "Exact URL Atlas attempted to visit.",
            ),
            (
                "effective_url",
                "Final URL after navigation or redirects, null if unresolved.",
            ),
            (
                "admitted_at",
                "Time the destination entered the crawl.",
            ),
            (
                "started_at",
                "Time acquisition began, null if it did not begin.",
            ),
            (
                "observed_at",
                "Time returned bytes were captured, null when none were.",
            ),
            (
                "finished_at",
                "Time the visit reached its terminal outcome.",
            ),
            ("outcome", "Final logical result of the visit."),
            (
                "status_code",
                "Final HTTP status when available.",
            ),
            (
                "document_id",
                "Document produced by the visit, null when none was retained.",
            ),
            ("provenance", "Typed origin of this observation."),
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
            ("kind", "Whether Atlas acquired or imported the evidence."),
            (
                "graph_id",
                "Stable crawl-plan identity, null for imports.",
            ),
            (
                "graph_config_hash",
                "Hash of the canonical frozen graph configuration.",
            ),
            (
                "graph_config",
                "Complete frozen graph configuration.",
            ),
            (
                "root_url_count",
                "Number of admitted roots represented by the crawl.",
            ),
            ("started_at", "Time crawl execution began."),
            ("finished_at", "Time crawl execution stopped."),
            ("stop_reason", "Reason the crawl stopped."),
        ),
    ),
    CatalogueObject(
        "view",
        "page_stats",
        "views/015_page_stats.sql",
        (
            "page_id",
            "visit_count",
            "document_count",
            "distinct_content_count",
            "first_observed_at",
            "last_observed_at",
            "inbound_link_count",
            "outbound_link_count",
        ),
        comment=(
            "Observation and directed-link evidence summarized by page."
        ),
        column_comments=(
            ("page_id", "Page identity being summarized."),
            ("visit_count", "Number of page observations."),
            (
                "document_count",
                "Number of page observations with a retained document.",
            ),
            (
                "distinct_content_count",
                "Number of distinct immutable content identities observed.",
            ),
            (
                "first_observed_at",
                "Earliest page observation time.",
            ),
            (
                "last_observed_at",
                "Latest page observation time.",
            ),
            (
                "inbound_link_count",
                "Number of distinct normalized pairs targeting this page.",
            ),
            (
                "outbound_link_count",
                "Number of distinct normalized pairs sourced from this page.",
            ),
        ),
    ),
    CatalogueObject(
        "view",
        "documents",
        "views/000_documents.sql",
        (
            "content_id",
            "node_count",
            "max_depth",
        ),
        schema=DOM_SCHEMA,
        comment="Canonical HTML DOM identities and costing statistics.",
        column_comments=(
            (
                "content_id",
                "Identity of the projected immutable HTML bytes.",
            ),
            ("node_count", "Number of elements in the canonical DOM."),
            (
                "max_depth",
                "Maximum element depth from the document root.",
            ),
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
        comment="Structural elements projected from immutable HTML content.",
        column_comments=(
            (
                "content_id",
                "Identity of the projected immutable HTML bytes.",
            ),
            (
                "element_index",
                "Zero-based element position in document order.",
            ),
            (
                "parent_index",
                "Parent element index, null for the root.",
            ),
            (
                "subtree_end_index",
                "Exclusive end of this element subtree.",
            ),
            ("depth", "Element depth from the root."),
            (
                "child_index",
                "Zero-based position among element siblings.",
            ),
            ("tag", "Normalized local tag name."),
            ("namespace", "Normalized element namespace."),
            ("attributes", "Attribute names and string values."),
            (
                "text_direct",
                "Text directly inside this element before child elements.",
            ),
            (
                "text_tail",
                "Text following this element within its parent.",
            ),
        ),
    ),
    CatalogueObject(
        "view",
        "jsonld",
        "views/021_jsonld.sql",
        ("content_id", "element_index", "type_terms", "value"),
        comment="Parsed JSON-LD payloads embedded in immutable HTML content.",
        column_comments=(
            (
                "content_id",
                "Identity of the containing immutable HTML bytes.",
            ),
            (
                "element_index",
                "Source script element in dom.elements.",
            ),
            (
                "type_terms",
                "Distinct raw @type strings found in the payload.",
            ),
            ("value", "Complete parsed JSON-LD payload."),
        ),
    ),
    CatalogueObject(
        "table_macro",
        "page_history",
        "macros_table/100_page_history.sql",
        (
            "page_id",
            "visit_id",
            "document_id",
            "content_id",
            "observed_at",
            "crawl_id",
            "requested_url",
            "effective_url",
            "outcome",
            "status_code",
        ),
        arguments_sql="NULL::UUID",
        parameters=(("selected_page_id", "UUID"),),
    ),
    CatalogueObject(
        "table_macro",
        "link_history",
        "macros_table/110_link_history.sql",
        (
            "link_id",
            "source_page_id",
            "target_page_id",
            "document_id",
            "content_id",
            "element_index",
            "raw_href",
            "observed_at",
        ),
        arguments_sql="NULL::UUID",
        parameters=(("selected_link_id", "UUID"),),
    ),
    CatalogueObject(
        "table_macro",
        "document",
        "macros_table/090_document.sql",
        (
            "content_id",
            "node_count",
            "max_depth",
            "nodes",
        ),
        arguments_sql="NULL::VARCHAR",
        parameters=(("selected_content_id", "VARCHAR"),),
        schema=DOM_SCHEMA,
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


def install_public_catalogue(catalogue: CatalogueConnection) -> None:
    """Atomically replace the complete current public SQL contract."""

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
        for item in PUBLIC_OBJECTS:
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
        for item in PUBLIC_OBJECTS
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
        for item in PUBLIC_OBJECTS
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

        expected_macros = {
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
        if actual_macros != expected_macros:
            errors.append(
                f"{schema} macros: expected {sorted(expected_macros)}, "
                f"got {sorted(actual_macros)}"
            )

    for item in PUBLIC_OBJECTS:
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
