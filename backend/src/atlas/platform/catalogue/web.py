"""Install and validate the portable ``web.*`` DuckLake catalogue."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from typing import Literal, Protocol

from atlas.platform.catalogue.exceptions import CatalogueSchemaError


WEB_CATALOGUE_VERSION = "1.0.0"
WEB_SCHEMA = "web"


class CatalogueConnection(Protocol):
    def remote_transaction(self): ...

    def trusted_remote_execute(self, sql: str) -> list[tuple]: ...

    def trusted_remote_rows(self, sql: str) -> list[tuple]: ...


@dataclass(frozen=True, slots=True)
class WebObject:
    kind: Literal["macro", "view", "table_macro"]
    name: str
    resource: str
    columns: tuple[str, ...] = ()


WEB_OBJECTS = (
    WebObject(
        "macro",
        "_catalogue_version",
        "macros_scalar/000_catalogue_version.sql",
    ),
    WebObject(
        "macro",
        "attribute",
        "macros_scalar/010_attribute.sql",
    ),
    WebObject(
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
    ),
    WebObject(
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
    ),
    WebObject(
        "view",
        "content",
        "views/003_content.sql",
        ("content_id", "content_bytes"),
    ),
    WebObject(
        "view",
        "page_observations",
        "views/010_page_observations.sql",
        ("page_id", "visit_id", "document_id", "observed_at"),
    ),
    WebObject(
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
    ),
    WebObject(
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
    ),
    WebObject(
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
    ),
    WebObject(
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
    ),
    WebObject(
        "view",
        "html",
        "views/020_html.sql",
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
            "text_content",
        ),
    ),
    WebObject(
        "view",
        "jsonld",
        "views/021_jsonld.sql",
        ("content_id", "element_index", "type_terms", "value"),
    ),
    WebObject(
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
    ),
    WebObject(
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
    ),
)


def install_web_catalogue(catalogue: CatalogueConnection) -> None:
    """Atomically replace the complete current ``web.*`` contract."""

    root = files("atlas.platform.catalogue").joinpath("sql", WEB_SCHEMA)
    with catalogue.remote_transaction():
        catalogue.trusted_remote_execute(
            f"CREATE SCHEMA IF NOT EXISTS {WEB_SCHEMA}"
        )
        for item in WEB_OBJECTS:
            sql = root.joinpath(item.resource).read_text(encoding="utf-8")
            catalogue.trusted_remote_execute(sql)


def validate_web_catalogue(catalogue: CatalogueConnection) -> None:
    """Fail when the installed semantic catalogue differs from the manifest."""

    expected_views = {
        item.name for item in WEB_OBJECTS if item.kind == "view"
    }
    actual_views = {
        str(row[0])
        for row in catalogue.trusted_remote_rows(
            "SELECT view_name FROM duckdb_views() "
            f"WHERE schema_name = '{WEB_SCHEMA}'"
        )
    }
    errors: list[str] = []
    if actual_views != expected_views:
        errors.append(
            f"web views: expected {sorted(expected_views)}, "
            f"got {sorted(actual_views)}"
        )

    expected_macros = {
        (item.name, item.kind)
        for item in WEB_OBJECTS
        if item.kind in {"macro", "table_macro"}
    }
    actual_macros = {
        (str(row[0]), str(row[1]))
        for row in catalogue.trusted_remote_rows(
            "SELECT function_name, function_type FROM duckdb_functions() "
            f"WHERE schema_name = '{WEB_SCHEMA}' "
            "AND function_type IN ('macro', 'table_macro')"
        )
    }
    if actual_macros != expected_macros:
        errors.append(
            f"web macros: expected {sorted(expected_macros)}, "
            f"got {sorted(actual_macros)}"
        )

    for item in WEB_OBJECTS:
        if not item.columns:
            continue
        try:
            if item.kind == "view":
                rows = catalogue.trusted_remote_rows(
                    f"DESCRIBE {WEB_SCHEMA}.{item.name}"
                )
            else:
                rows = catalogue.trusted_remote_rows(
                    f"DESCRIBE SELECT * FROM {WEB_SCHEMA}.{item.name}"
                    "(NULL::UUID)"
                )
        except Exception as exc:
            errors.append(f"web.{item.name}: {exc}")
            continue
        actual_columns = tuple(str(row[0]) for row in rows)
        if actual_columns != item.columns:
            errors.append(
                f"web.{item.name}: expected columns {item.columns}, "
                f"got {actual_columns}"
            )

    try:
        rows = catalogue.trusted_remote_rows(
            f"SELECT {WEB_SCHEMA}._catalogue_version()"
        )
        actual_version = str(rows[0][0])
    except Exception as exc:
        errors.append(f"web catalogue version: {exc}")
    else:
        if actual_version != WEB_CATALOGUE_VERSION:
            errors.append(
                f"web catalogue version: expected {WEB_CATALOGUE_VERSION}, "
                f"got {actual_version}"
            )

    if errors:
        raise CatalogueSchemaError("; ".join(errors))
