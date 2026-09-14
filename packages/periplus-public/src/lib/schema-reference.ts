// Public catalogue contract. Both schemas expose these columns.
export const schemaReference = [
  {
    "name": "public_v1.page",
    "grain": "Distinct requested, effective and linked URLs in retained HTML evidence, including uncaptured destinations.",
    "key": "url",
    "columns": [
      [
        "url",
        "VARCHAR",
        "Normalized URL identity. Redirects and canonical declarations do not merge pages."
      ]
    ]
  },
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
        "page_url",
        "VARCHAR",
        "Requested page URL; references page.url."
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
        "target_url",
        "VARCHAR",
        "Resolved normalized destination; references page.url."
      ],
      [
        "raw_href",
        "VARCHAR",
        "Original parsed href attribute value."
      ]
    ]
  }
] as const

export function sqlDraftLink(sql: string) {
  return `/sql?${new URLSearchParams({ sql })}`
}
