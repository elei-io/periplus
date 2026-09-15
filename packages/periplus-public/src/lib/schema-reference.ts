// Public ClickHouse catalogue.
export const schemaReference = [
  {
    "name": "public_v1.page",
    "grain": "Distinct requested, effective and linked URLs in retained HTML evidence, including uncaptured destinations.",
    "key": "url",
    "columns": [
      [
        "url",
        "String",
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
        "String",
        "Requested page URL; references page.url."
      ],
      [
        "effective_url",
        "String",
        "Final URL after navigation."
      ],
      [
        "captured_at",
        "DateTime64(6, 'UTC')",
        "Time the page content was captured."
      ],
      [
        "http_status_code",
        "UInt32",
        "HTTP response status when known; retained error bodies qualify."
      ],
      [
        "content_id",
        "String",
        "SHA-256 identity of retained bytes."
      ],
      [
        "byte_length",
        "UInt64",
        "Length of logical bytes before storage compression."
      ],
      [
        "encoding",
        "String",
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
        "String",
        "SHA-256 identity of captured bytes."
      ],
      [
        "node_index",
        "UInt32",
        "Zero-based depth-first node position, scoped to the catalogue snapshot."
      ],
      [
        "parent_index",
        "UInt32",
        "Nearest parent element position; null for the document element."
      ],
      [
        "subtree_end_index",
        "UInt32",
        "Exclusive end of this node's subtree."
      ],
      [
        "sibling_index",
        "UInt32",
        "Zero-based position among projected element siblings."
      ],
      [
        "depth",
        "UInt32",
        "Element-parent depth; document element is zero."
      ],
      [
        "tag",
        "String",
        "Local element tag name."
      ],
      [
        "namespace",
        "String",
        "Namespace URI when applicable."
      ],
      [
        "attributes",
        "Map(String, String)",
        "Attribute map; namespaced keys use {namespace-uri}local-name."
      ],
      [
        "text_direct",
        "String",
        "Immediate child text concatenated in order, without normalization."
      ],
      [
        "text",
        "String",
        "All descendant text nodes concatenated in document order; empty when absent. Preserves whitespace, includes template fragments and script/style/title text, inserts no separators, ignores comments and CSS visibility."
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
        "UInt32",
        "Anchor node position in that capture's content."
      ],
      [
        "target_url",
        "String",
        "Resolved normalized destination; references page.url."
      ],
      [
        "raw_href",
        "String",
        "Original parsed href attribute value."
      ]
    ]
  }
] as const

export function sqlDraftLink(sql: string) {
  return `/sql?${new URLSearchParams({ sql })}`
}
