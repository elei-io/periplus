// Public v1 contract. Keep aligned with public_registry.py.
export const schemaReference = [
  {
    "name": "public_v1.capture",
    "grain": "Acquisitions with retained content, including retained HTTP error responses.",
    "key": "capture_id",
    "columns": [
      ["request_ids", "UUID[]", "Sorted unique coverage request IDs supplied with this capture; empty until membership evidence arrives."],
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
      ["byte_length", "BIGINT", "Length of logical bytes before storage compression."],
      [
        "representation",
        "VARCHAR",
        "Meaning of the captured representation."
      ],
      [
        "media_type",
        "VARCHAR",
        "Detected media type."
      ],
      [
        "encoding",
        "VARCHAR",
        "Detected character encoding when meaningful."
      ]
    ]
  },
  {
    "name": "public_v1.html_node",
    "grain": "Complete HTML5 parsed document nodes.",
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
        "Parent node position; null for the document root."
      ],
      [
        "subtree_end_index",
        "INTEGER",
        "Exclusive end of this node's subtree."
      ],
      [
        "sibling_index",
        "INTEGER",
        "Zero-based position among all sibling nodes."
      ],
      [
        "node_type",
        "VARCHAR",
        "document, doctype, element, text, comment or processing_instruction."
      ],
      [
        "name",
        "VARCHAR",
        "Local element/doctype name or processing instruction target."
      ],
      [
        "namespace",
        "VARCHAR",
        "Namespace URI when applicable."
      ],
      [
        "value",
        "VARCHAR",
        "Text, comment or processing instruction content."
      ]
    ]
  },
  {
    "name": "public_v1.html_element",
    "grain": "HTML elements sharing identity and positions with html_node.",
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
        "Parent node position; null for the document root."
      ],
      [
        "subtree_end_index",
        "INTEGER",
        "Exclusive end of this node's subtree."
      ],
      [
        "sibling_index",
        "INTEGER",
        "Zero-based position among all sibling nodes."
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
      ]
    ]
  },
  {
    "name": "public_v1.link_occurrence",
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
