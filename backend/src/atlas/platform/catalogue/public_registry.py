"""Lightweight manifest for the runtime ``web.*`` and ``dom.*`` API."""

from atlas.platform.catalogue.public import CatalogueObject


def _comments(
    *items: tuple[str, str],
) -> tuple[tuple[str, str], ...]:
    return items


_HTML = frozenset({"material.html_elements"})
_JSONLD = frozenset({"material.jsonld_values"})
_LINKS = frozenset({"material.link_occurrences"})
_DOM_COLUMNS = (
    "content_id", "element_index", "parent_index", "subtree_end_index",
    "depth", "child_index", "tag", "namespace", "attributes",
    "text_direct", "text_tail",
)
_DOM_COMMENTS = _comments(
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
)

PUBLIC_OBJECTS = (
    CatalogueObject(
        "macro", "_catalogue_version",
        "macros_scalar/000_catalogue_version.sql",
        return_type="VARCHAR", exposed=False,
    ),
    CatalogueObject(
        "view", "page", "views/001_page.sql",
        (
            "url", "scheme", "hostname", "port", "path", "query",
            "latest_visit_id", "latest_finished_at",
        ),
        comment="Canonical normalized URL identities observed through visits.",
        column_comments=_comments(
            ("url", "Unique normalized URL represented by this page."),
            ("scheme", "Normalized URL scheme."),
            ("hostname", "Normalized hostname."),
            ("port", "Explicit non-default port, otherwise null."),
            ("path", "Normalized URL path."),
            ("query", "Preserved query string, null when absent."),
            ("latest_visit_id", "Deterministically latest visit for this URL."),
            ("latest_finished_at", "Terminal time of the latest visit."),
        ),
    ),
    CatalogueObject(
        "view", "visit", "views/010_visit.sql",
        (
            "visit_id", "crawl_id", "requested_url", "effective_url",
            "admitted_at", "started_at", "observed_at", "finished_at",
            "outcome", "status_code", "document_id", "content_id",
            "content_bytes", "representation", "declared_media_type",
            "detected_media_type", "charset", "provenance",
        ),
        comment="Acquisition history with retained document evidence.",
        column_comments=_comments(
            ("visit_id", "Unique identity of this acquisition visit."),
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
            ("provenance", "Typed origin of this visit evidence."),
        ),
    ),
    CatalogueObject(
        "view", "crawls", "views/014_crawls.sql",
        (
            "crawl_id", "kind", "graph_id", "graph_config_hash",
            "graph_config", "root_url_count", "started_at", "finished_at",
            "stop_reason",
        ),
        comment="Terminal crawl execution evidence.",
        column_comments=_comments(
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
        "macro", "get_attribute",
        "macros_scalar/010_get_attribute.sql",
        parameters=(
            ("element_attributes", "MAP(VARCHAR, VARCHAR)"),
            ("attribute_name", "VARCHAR"),
        ),
        return_type="VARCHAR", schema="dom",
    ),
    CatalogueObject(
        "view", "elements", "views/001_elements.sql", _DOM_COLUMNS,
        schema="dom", comment="Structural DOM elements keyed by immutable content.",
        column_comments=_DOM_COMMENTS, requires_relations=_HTML,
    ),
    CatalogueObject(
        "view", "stats", "views/002_stats.sql",
        ("content_id", "element_count", "max_depth"), schema="dom",
        comment="DOM statistics calculated explicitly from structural elements.",
        column_comments=_comments(
            ("content_id", "Immutable HTML content identity."),
            ("element_count", "Number of projected elements."),
            ("max_depth", "Maximum projected element depth."),
        ),
        requires_relations=_HTML,
    ),
    CatalogueObject(
        "table_macro", "text_content", "macros_table/100_text_content.sql",
        ("content_id", "element_index", "text_content"),
        arguments_sql="NULL::VARCHAR, NULL::INTEGER",
        parameters=(
            ("selected_content_id", "VARCHAR"),
            ("selected_element_index", "INTEGER"),
        ),
        schema="dom", requires_relations=_HTML,
    ),
    CatalogueObject(
        "table_macro", "query_selector",
        "macros_table/110_query_selector.sql", _DOM_COLUMNS,
        arguments_sql="NULL::VARCHAR, 'a'::VARCHAR",
        parameters=(
            ("selected_content_id", "VARCHAR"),
            ("css_selector", "VARCHAR"),
        ),
        schema="dom", requires_relations=_HTML,
        requires_functions=frozenset({"atlas_dom_select_first"}),
    ),
    CatalogueObject(
        "table_macro", "query_selector_all",
        "macros_table/120_query_selector_all.sql", _DOM_COLUMNS,
        arguments_sql="NULL::VARCHAR, 'a'::VARCHAR",
        parameters=(
            ("selected_content_id", "VARCHAR"),
            ("css_selector", "VARCHAR"),
        ),
        schema="dom", requires_relations=_HTML,
        requires_functions=frozenset({"atlas_dom_select_all"}),
    ),
    CatalogueObject(
        "view", "jsonld", "views/021_jsonld.sql",
        ("content_id", "element_index", "type_terms", "value"),
        comment="Parsed JSON-LD values embedded in immutable HTML content.",
        column_comments=_comments(
            ("content_id", "Immutable HTML content identity."),
            ("element_index", "Source script element index."),
            ("type_terms", "Distinct raw JSON-LD @type terms."),
            ("value", "Complete parsed JSON-LD value."),
        ),
        requires_relations=_JSONLD,
    ),
    CatalogueObject(
        "view", "link_occurrence", "views/011_link_occurrence.sql",
        (
            "occurrence_id", "link_id", "visit_id", "document_id",
            "content_id", "element_index", "observed_at", "raw_href",
            "source_url", "target_url", "relation_scope",
        ),
        comment="Exact DOM occurrences supporting canonical page links.",
        column_comments=_comments(
            ("occurrence_id", "Stable identity of this link occurrence."),
            ("link_id", "Canonical directed link supported by this occurrence."),
            ("visit_id", "Visit during which this occurrence was retained."),
            ("document_id", "Document observation containing the element."),
            ("content_id", "Immutable content containing the element."),
            ("element_index", "Source element position."),
            ("observed_at", "Time the containing representation was captured."),
            ("raw_href", "Exact href before resolution and normalization."),
            ("source_url", "Normalized URL containing the source element."),
            ("target_url", "Normalized target resolved in visit context."),
            ("relation_scope", "Most-specific source-target relationship."),
        ),
        requires_relations=_LINKS,
    ),
    CatalogueObject(
        "view", "link", "views/002_link.sql",
        (
            "link_id", "source_url", "target_url", "relation_scope",
            "first_seen_at", "last_seen_at", "visit_count",
            "distinct_content_count", "occurrence_count",
        ),
        comment="Canonical directed page links with retained history rollups.",
        column_comments=_comments(
            ("link_id", "Deterministic identity of the directed page pair."),
            ("source_url", "Normalized source URL."),
            ("target_url", "Normalized resolved target URL."),
            ("relation_scope", "Most-specific source-target relationship."),
            ("first_seen_at", "Earliest retained occurrence time."),
            ("last_seen_at", "Latest retained occurrence time."),
            ("visit_count", "Visits containing this link."),
            ("distinct_content_count", "Distinct contents containing this link."),
            ("occurrence_count", "Retained DOM occurrences of this link."),
        ),
        requires_relations=_LINKS,
    ),
)
