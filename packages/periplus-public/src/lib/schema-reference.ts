// Public v2 contract. Keep aligned with public_registry.py.
export const schemaReference = [
  {
    "name": "public_v1.capture",
    "grain": "Acquisitions with retained HTML, including retained HTML HTTP error responses.",
    "key": "capture_id",
    "columns": [
      [
        "capture_id",
        "UUID",
        "Acquisition identity with retained content."
      ],
      [
        "requested_url",
        "VARCHAR",
        "Normalized requested URL."
      ],
      [
        "effective_url",
        "VARCHAR",
        "Final URL after navigation."
      ],
      [
        "captured_at",
        "TIMESTAMPTZ",
        "Time the page content was captured."
      ],
      [
        "http_status_code",
        "INTEGER",
        "HTTP response status when known; retained error bodies qualify."
      ],
      [
        "content_id",
        "VARCHAR",
        "SHA-256 identity of retained bytes."
      ],
      [
        "byte_length",
        "BIGINT",
        "Length of logical bytes before storage compression."
      ],
      [
        "encoding",
        "VARCHAR",
        "Detected character encoding when meaningful."
      ],
      [
        "request_ids",
        "UUID[]",
        "Sorted unique coverage request IDs supplied with this capture; empty until membership evidence arrives."
      ]
    ]
  },
  {
    "name": "public_v1.html_element",
    "grain": "Parsed HTML elements with materialized complete text.",
    "key": "content_id + node_index",
    "columns": [
      [
        "content_id",
        "VARCHAR",
        "SHA-256 identity of captured bytes."
      ],
      [
        "node_index",
        "INTEGER",
        "Zero-based depth-first node position, scoped to the catalogue snapshot."
      ],
      [
        "parent_index",
        "INTEGER",
        "Nearest parent element position; null for the document element."
      ],
      [
        "subtree_end_index",
        "INTEGER",
        "Exclusive end of this node's subtree."
      ],
      [
        "sibling_index",
        "INTEGER",
        "Zero-based position among projected element siblings."
      ],
      [
        "depth",
        "INTEGER",
        "Element-parent depth; document element is zero."
      ],
      [
        "tag",
        "VARCHAR",
        "Local element tag name."
      ],
      [
        "namespace",
        "VARCHAR",
        "Namespace URI when applicable."
      ],
      [
        "attributes",
        "MAP(VARCHAR, VARCHAR)",
        "Attribute map; namespaced keys use {namespace-uri}local-name."
      ],
      [
        "text_direct",
        "VARCHAR",
        "Immediate child text concatenated in order, without normalization."
      ],
      [
        "text",
        "VARCHAR",
        "All descendant text nodes concatenated in document order; empty when absent. Preserves whitespace, includes template fragments and script/style/title text, inserts no separators, ignores comments and CSS visibility."
      ]
    ]
  },
  {
    "name": "public_v1.html_form",
    "grain": "HTML form elements with declared attributes.",
    "key": "content_id + node_index",
    "columns": [
      [
        "content_id",
        "VARCHAR",
        "SHA-256 identity of captured bytes."
      ],
      [
        "node_index",
        "INTEGER",
        "Source form node position."
      ],
      [
        "id",
        "VARCHAR",
        "Declared id attribute."
      ],
      [
        "name",
        "VARCHAR",
        "Declared name attribute."
      ],
      [
        "action",
        "VARCHAR",
        "Declared action, without URL resolution or defaults."
      ],
      [
        "method",
        "VARCHAR",
        "Declared method, without normalization or defaults."
      ],
      [
        "enctype",
        "VARCHAR",
        "Declared enctype attribute."
      ],
      [
        "target",
        "VARCHAR",
        "Declared target attribute."
      ]
    ]
  },
  {
    "name": "public_v1.html_form_control",
    "grain": "Native HTML form controls, including controls without a form owner.",
    "key": "content_id + node_index",
    "columns": [
      [
        "content_id",
        "VARCHAR",
        "SHA-256 identity of captured bytes."
      ],
      [
        "node_index",
        "INTEGER",
        "Source native form-control node position."
      ],
      [
        "form_node_index",
        "INTEGER",
        "Form owner reconstructed from explicit form reference or nearest ancestor; null when unowned."
      ],
      [
        "tag",
        "VARCHAR",
        "Source input, button, select, textarea, fieldset, output or object tag."
      ],
      [
        "type",
        "VARCHAR",
        "Declared type attribute, without defaults."
      ],
      [
        "name",
        "VARCHAR",
        "Declared name attribute."
      ],
      [
        "value",
        "VARCHAR",
        "Parsed textarea child text, otherwise the declared value attribute; not live state."
      ],
      [
        "required",
        "BOOLEAN",
        "Whether the required attribute is present."
      ],
      [
        "disabled",
        "BOOLEAN",
        "Whether the disabled attribute is present on this element."
      ],
      [
        "readonly",
        "BOOLEAN",
        "Whether the readonly attribute is present."
      ],
      [
        "multiple",
        "BOOLEAN",
        "Whether the multiple attribute is present."
      ]
    ]
  },
  {
    "name": "public_v1.html_select_option",
    "grain": "HTML options owned by select elements, including optgroup descendants.",
    "key": "content_id + node_index",
    "columns": [
      [
        "content_id",
        "VARCHAR",
        "SHA-256 identity of captured bytes."
      ],
      [
        "node_index",
        "INTEGER",
        "Source option node position."
      ],
      [
        "select_node_index",
        "INTEGER",
        "Nearest owning select node position."
      ],
      [
        "option_index",
        "INTEGER",
        "Zero-based source position within the select, across optgroups."
      ],
      [
        "value",
        "VARCHAR",
        "Declared value attribute; no text fallback."
      ],
      [
        "text",
        "VARCHAR",
        "Ordered descendant text without normalization."
      ],
      [
        "selected",
        "BOOLEAN",
        "Whether the selected attribute is present; not live selectedness."
      ],
      [
        "disabled",
        "BOOLEAN",
        "Whether disabled is present on this option; not inherited state."
      ]
    ]
  },
  {
    "name": "public_v1.html_list",
    "grain": "HTML ordered and unordered lists, including empty lists.",
    "key": "content_id + node_index",
    "columns": [
      [
        "content_id",
        "VARCHAR",
        "SHA-256 identity of captured bytes."
      ],
      [
        "node_index",
        "INTEGER",
        "Source ul or ol node position."
      ],
      [
        "ordered",
        "BOOLEAN",
        "True for ol."
      ],
      [
        "start_number",
        "BIGINT",
        "Effective ordered-list starting number; null for ul."
      ],
      [
        "reversed",
        "BOOLEAN",
        "True when an ol has the reversed attribute."
      ]
    ]
  },
  {
    "name": "public_v1.html_list_item",
    "grain": "Direct HTML list items with source identity and effective numbering.",
    "key": "content_id + node_index",
    "columns": [
      [
        "content_id",
        "VARCHAR",
        "SHA-256 identity of captured bytes."
      ],
      [
        "node_index",
        "INTEGER",
        "Source li node position."
      ],
      [
        "list_node_index",
        "INTEGER",
        "Direct owning ul or ol node position."
      ],
      [
        "item_index",
        "INTEGER",
        "Zero-based position among the list's direct HTML li children."
      ],
      [
        "ordinal",
        "BIGINT",
        "Effective ordered-list number after start, reversed and value; null for ul."
      ],
      [
        "text",
        "VARCHAR",
        "Ordered descendant text excluding nested ul/ol lists; empty for an empty item."
      ]
    ]
  },
  {
    "name": "public_v1.html_jsonld",
    "grain": "Materialized embedded JSON-LD declarations, including invalid scripts, without semantic expansion.",
    "key": "content_id + node_index",
    "columns": [
      [
        "content_id",
        "VARCHAR",
        "SHA-256 identity of captured bytes."
      ],
      [
        "node_index",
        "INTEGER",
        "Source application/ld+json script node position."
      ],
      [
        "value",
        "JSON",
        "Complete document parsed as DuckDB JSON; SQL null on parse failure."
      ],
      [
        "parse_error",
        "VARCHAR",
        "Empty JSON-LD script or Invalid JSON syntax; null on successful parsing."
      ]
    ]
  },
  {
    "name": "public_v1.html_image",
    "grain": "HTML img elements with original parsed attributes, including images without src.",
    "key": "content_id + node_index",
    "columns": [
      [
        "content_id",
        "VARCHAR",
        "SHA-256 identity of captured bytes."
      ],
      [
        "node_index",
        "INTEGER",
        "Source img node position."
      ],
      [
        "src",
        "VARCHAR",
        "Declared src value; relative references remain relative."
      ],
      [
        "srcset",
        "VARCHAR",
        "Declared srcset value without parsing or candidate selection."
      ],
      [
        "sizes",
        "VARCHAR",
        "Declared sizes value."
      ],
      [
        "alt",
        "VARCHAR",
        "Declared alternative text; empty and missing remain distinct."
      ],
      [
        "width",
        "VARCHAR",
        "Declared width as a source string, not a measured dimension."
      ],
      [
        "height",
        "VARCHAR",
        "Declared height as a source string, not a measured dimension."
      ]
    ]
  },
  {
    "name": "public_v1.html_metadata",
    "grain": "Explicit HTML metadata declarations, preserving source nodes and repeated declarations.",
    "key": "content_id + node_index + kind + name",
    "columns": [
      [
        "content_id",
        "VARCHAR",
        "SHA-256 identity of captured bytes."
      ],
      [
        "node_index",
        "INTEGER",
        "Source metadata element node position."
      ],
      [
        "kind",
        "VARCHAR",
        "Declaration kind: title, meta_name, meta_property, meta_http_equiv, meta_charset, link_rel or html_attribute."
      ],
      [
        "name",
        "VARCHAR",
        "Declared metadata name or relation token; title, charset and lang use fixed names."
      ],
      [
        "value",
        "VARCHAR",
        "Parsed declared value without normalization; null when the value attribute is absent."
      ]
    ]
  },
  {
    "name": "public_v1.html_section",
    "grain": "Heading-delimited source passages; inferred ranges, not semantic or CSS sections.",
    "key": "content_id + heading_node_index",
    "columns": [
      [
        "content_id",
        "VARCHAR",
        "SHA-256 identity of captured bytes."
      ],
      [
        "heading_node_index",
        "INTEGER",
        "Heading that starts this passage."
      ],
      [
        "parent_heading_node_index",
        "INTEGER",
        "Nearest preceding heading of higher rank; null when absent."
      ],
      [
        "start_node_index",
        "INTEGER",
        "Inclusive passage start immediately after the heading subtree."
      ],
      [
        "end_node_index",
        "INTEGER",
        "Exclusive end at the next heading of equal/higher rank, or document end."
      ]
    ]
  },
  {
    "name": "public_v1.html_code",
    "grain": "HTML code elements with source identity and complete descendant text.",
    "key": "content_id + node_index",
    "columns": [
      [
        "content_id",
        "VARCHAR",
        "SHA-256 identity of captured bytes."
      ],
      [
        "node_index",
        "INTEGER",
        "Source code element node position."
      ],
      [
        "block",
        "BOOLEAN",
        "True when the code element has an HTML pre ancestor; not CSS display state."
      ],
      [
        "text",
        "VARCHAR",
        "Ordered descendant text preserving whitespace and line breaks; empty for empty code."
      ]
    ]
  },
  {
    "name": "public_v1.html_heading",
    "grain": "HTML headings with source identity and complete descendant text.",
    "key": "content_id + node_index",
    "columns": [
      [
        "content_id",
        "VARCHAR",
        "SHA-256 identity of captured bytes."
      ],
      [
        "node_index",
        "INTEGER",
        "Source h1 through h6 node position."
      ],
      [
        "level",
        "INTEGER",
        "Declared HTML heading level, from 1 through 6."
      ],
      [
        "text",
        "VARCHAR",
        "Ordered descendant text without normalization; empty for an empty heading."
      ]
    ]
  },
  {
    "name": "public_v1.html_table",
    "grain": "HTML tables, including empty and nested tables.",
    "key": "content_id + node_index",
    "columns": [
      [
        "content_id",
        "VARCHAR",
        "SHA-256 identity of captured bytes."
      ],
      [
        "node_index",
        "INTEGER",
        "Source table node position."
      ],
      [
        "caption_node_index",
        "INTEGER",
        "First direct caption node; null when absent."
      ],
      [
        "caption",
        "VARCHAR",
        "Caption descendant text excluding nested tables; null when absent."
      ]
    ]
  },
  {
    "name": "public_v1.html_table_cell",
    "grain": "One source HTML cell per row with span-aware grid positions, computed on demand.",
    "key": "content_id + node_index",
    "columns": [
      [
        "content_id",
        "VARCHAR",
        "SHA-256 identity of captured bytes."
      ],
      [
        "table_node_index",
        "INTEGER",
        "Owning table node position."
      ],
      [
        "row_node_index",
        "INTEGER",
        "Owning tr node position."
      ],
      [
        "node_index",
        "INTEGER",
        "Source td or th node position."
      ],
      [
        "row_index",
        "INTEGER",
        "Zero-based row in parsed source order, including empty rows."
      ],
      [
        "column_index",
        "INTEGER",
        "Zero-based starting grid column, accounting for spans."
      ],
      [
        "row_span",
        "INTEGER",
        "Effective row span, bounded by the source row group."
      ],
      [
        "column_span",
        "INTEGER",
        "Effective column span."
      ],
      [
        "is_header",
        "BOOLEAN",
        "True for a th source element."
      ],
      [
        "text",
        "VARCHAR",
        "Ordered descendant text excluding nested tables; empty for an empty cell."
      ]
    ]
  },
  {
    "name": "public_v1.link",
    "grain": "HTTP(S) anchor occurrences resolved in capture context.",
    "key": "capture_id + node_index",
    "columns": [
      [
        "capture_id",
        "UUID",
        "Capture in which the hyperlink was resolved."
      ],
      [
        "node_index",
        "INTEGER",
        "Anchor node position in that capture's content."
      ],
      [
        "raw_href",
        "VARCHAR",
        "Original parsed href attribute value."
      ],
      [
        "resolved_url",
        "VARCHAR",
        "Resolved normalized HTTP(S) URL."
      ]
    ]
  }
] as const

export function sqlDraftLink(sql: string) {
  return `/sql?${new URLSearchParams({ sql })}`
}
