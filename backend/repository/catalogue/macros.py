"""Persistent SQL helpers over the truthful Atlas catalogue projection."""

from __future__ import annotations

from repository.catalogue.client import Catalogue

_HTML_NAMESPACE = "http://www.w3.org/1999/xhtml"
_VOID_TAGS = (
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
)
_RAW_TEXT_TAGS = ("script", "style")
_READABLE_BREAK_TAGS = (
    "address",
    "article",
    "aside",
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
)


def install_catalogue_macros(catalogue: Catalogue) -> None:
    """Create the read-only DOM-style helpers exposed to catalogue SQL."""

    namespace = ".".join(
        _quote_identifier(part)
        for part in (catalogue.config.alias, catalogue.config.schema)
    )
    elements = f"{namespace}.{_quote_identifier('elements')}"
    void_tags = _sql_strings(_VOID_TAGS)
    raw_text_tags = _sql_strings(_RAW_TEXT_TAGS)
    readable_break_tags = _sql_strings(_READABLE_BREAK_TAGS)

    statements = (
        f"""
        CREATE OR REPLACE MACRO {namespace}.get_attribute(attrs, attribute_name) AS (
            map_extract_value(
                attrs,
                CASE
                    WHEN starts_with(attribute_name, '{{') THEN attribute_name
                    ELSE lower(attribute_name)
                END
            )
        )
        """,
        f"""
        CREATE OR REPLACE MACRO {namespace}.has_attribute(attrs, attribute_name) AS (
            map_contains(
                attrs,
                CASE
                    WHEN starts_with(attribute_name, '{{') THEN attribute_name
                    ELSE lower(attribute_name)
                END
            )
        )
        """,
        f"""
        CREATE OR REPLACE MACRO {namespace}.has_text(value) AS (
            coalesce(regexp_matches(value, '[^ \\t\\r\\n\\f]'), false)
        )
        """,
        f"""
        CREATE OR REPLACE MACRO {namespace}.text_content(p_document_id, p_element_index) AS (
            WITH parameters AS (
                SELECT
                    p_document_id AS requested_document_id,
                    p_element_index AS requested_element_index
            ),
            root AS (
                SELECT
                    parameters.requested_document_id,
                    source.element_index,
                    source.subtree_end_index
                FROM {elements} AS source, parameters
                WHERE source.document_id = parameters.requested_document_id
                  AND source.element_index = parameters.requested_element_index
            ),
            fragments AS (
                SELECT
                    child.element_index AS event_index,
                    0 AS event_phase,
                    child.depth,
                    child.text_direct AS fragment
                FROM {elements} AS child, root
                WHERE child.document_id = root.requested_document_id
                  AND child.element_index BETWEEN root.element_index AND root.subtree_end_index

                UNION ALL

                SELECT
                    child.subtree_end_index AS event_index,
                    1 AS event_phase,
                    child.depth,
                    child.text_tail AS fragment
                FROM {elements} AS child, root
                WHERE child.document_id = root.requested_document_id
                  AND child.element_index > root.element_index
                  AND child.element_index <= root.subtree_end_index
            )
            SELECT coalesce(
                string_agg(
                    fragment,
                    '' ORDER BY event_index, event_phase, depth DESC
                ) FILTER (WHERE fragment <> ''),
                ''
            )
            FROM fragments
        )
        """,
        f"""
        CREATE OR REPLACE MACRO {namespace}.inner_html(p_document_id, p_element_index) AS (
            WITH parameters AS (
                SELECT
                    p_document_id AS requested_document_id,
                    p_element_index AS requested_element_index
            ),
            root AS (
                SELECT
                    parameters.requested_document_id,
                    source.element_index,
                    source.subtree_end_index,
                    source.tag
                FROM {elements} AS source, parameters
                WHERE source.document_id = parameters.requested_document_id
                  AND source.element_index = parameters.requested_element_index
            ),
            events AS (
                SELECT
                    child.element_index AS event_index,
                    0 AS event_phase,
                    child.depth,
                    0 AS depth_phase,
                    '<' || child.tag || coalesce((
                        SELECT string_agg(
                            ' ' || entry.key || '="' ||
                            replace(replace(replace(replace(
                                entry.value,
                                '&', '&amp;'
                            ), '"', '&quot;'), '<', '&lt;'), '>', '&gt;') || '"',
                            '' ORDER BY entry.key
                        )
                        FROM unnest(map_entries(child.attributes)) AS attribute(entry)
                    ), '') || '>' AS fragment
                FROM {elements} AS child, root
                WHERE child.document_id = root.requested_document_id
                  AND child.element_index > root.element_index
                  AND child.element_index <= root.subtree_end_index

                UNION ALL

                SELECT
                    child.element_index AS event_index,
                    1 AS event_phase,
                    child.depth,
                    0 AS depth_phase,
                    CASE
                        WHEN child.namespace_uri = {_sql_string(_HTML_NAMESPACE)}
                         AND child.tag IN ({raw_text_tags})
                            THEN child.text_direct
                        ELSE replace(replace(replace(
                            child.text_direct,
                            '&', '&amp;'
                        ), '<', '&lt;'), '>', '&gt;')
                    END AS fragment
                FROM {elements} AS child, root
                WHERE child.document_id = root.requested_document_id
                  AND child.element_index BETWEEN root.element_index AND root.subtree_end_index

                UNION ALL

                SELECT
                    child.subtree_end_index AS event_index,
                    2 AS event_phase,
                    child.depth,
                    0 AS depth_phase,
                    '</' || child.tag || '>' AS fragment
                FROM {elements} AS child, root
                WHERE child.document_id = root.requested_document_id
                  AND child.element_index > root.element_index
                  AND child.element_index <= root.subtree_end_index
                  AND NOT (
                      child.namespace_uri = {_sql_string(_HTML_NAMESPACE)}
                      AND child.tag IN ({void_tags})
                  )

                UNION ALL

                SELECT
                    child.subtree_end_index AS event_index,
                    2 AS event_phase,
                    child.depth,
                    1 AS depth_phase,
                    replace(replace(replace(
                        child.text_tail,
                        '&', '&amp;'
                    ), '<', '&lt;'), '>', '&gt;') AS fragment
                FROM {elements} AS child, root
                WHERE child.document_id = root.requested_document_id
                  AND child.element_index > root.element_index
                  AND child.element_index <= root.subtree_end_index
            )
            SELECT coalesce(
                string_agg(
                    fragment,
                    '' ORDER BY event_index, event_phase, depth DESC, depth_phase
                ) FILTER (WHERE fragment <> ''),
                ''
            )
            FROM events
        )
        """,
        f"""
        CREATE OR REPLACE MACRO {namespace}.readable_text(p_document_id, p_element_index) AS (
            WITH parameters AS (
                SELECT
                    p_document_id AS requested_document_id,
                    p_element_index AS requested_element_index
            ),
            root AS (
                SELECT
                    parameters.requested_document_id,
                    source.element_index,
                    source.subtree_end_index
                FROM {elements} AS source, parameters
                WHERE source.document_id = parameters.requested_document_id
                  AND source.element_index = parameters.requested_element_index
            ),
            fragments AS (
                SELECT
                    child.element_index AS event_index,
                    0 AS event_phase,
                    child.depth,
                    0 AS depth_phase,
                    child.text_direct AS fragment
                FROM {elements} AS child, root
                WHERE child.document_id = root.requested_document_id
                  AND child.element_index BETWEEN root.element_index AND root.subtree_end_index

                UNION ALL

                SELECT
                    child.subtree_end_index AS event_index,
                    1 AS event_phase,
                    child.depth,
                    0 AS depth_phase,
                    CASE WHEN child.tag IN ({readable_break_tags}) THEN ' ' ELSE '' END AS fragment
                FROM {elements} AS child, root
                WHERE child.document_id = root.requested_document_id
                  AND child.element_index > root.element_index
                  AND child.element_index <= root.subtree_end_index

                UNION ALL

                SELECT
                    child.subtree_end_index AS event_index,
                    1 AS event_phase,
                    child.depth,
                    1 AS depth_phase,
                    child.text_tail AS fragment
                FROM {elements} AS child, root
                WHERE child.document_id = root.requested_document_id
                  AND child.element_index > root.element_index
                  AND child.element_index <= root.subtree_end_index
            )
            SELECT trim(regexp_replace(
                coalesce(string_agg(
                    fragment,
                    '' ORDER BY event_index, event_phase, depth DESC, depth_phase
                ), ''),
                '\\s+',
                ' ',
                'g'
            ))
            FROM fragments
        )
        """,
    )
    for statement in statements:
        catalogue.connection.execute(statement)


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sql_strings(values: tuple[str, ...]) -> str:
    return ", ".join(_sql_string(value) for value in values)
