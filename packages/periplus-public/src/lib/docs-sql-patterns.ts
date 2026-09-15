export const docsSqlPatterns = [
  {
    "title": "Find pages without a requested capture",
    "description": "Find link and redirect destinations with no retained HTML capture requested at that URL.",
    "sql": "SELECT p.url\nFROM public_v1.page p\nWHERE NOT EXISTS (SELECT 1 FROM public_v1.capture c WHERE c.page_url = p.url)\nORDER BY p.url LIMIT 20;"
  },
  {
    "title": "Choose pages to explore",
    "description": "Select retained captures, then use their content IDs to inspect HTML elements.",
    "sql": "SELECT content_id, effective_url, captured_at FROM capture ORDER BY captured_at DESC, capture_id LIMIT 20;"
  },
  {
    "title": "Find matching HTML headings",
    "description": "Complete descendant text preserves parsed whitespace and inline words. Script and style text are included when inside the selected element.",
    "sql": "SELECT content_id, node_index, tag, text FROM html_element WHERE tag = 'h1' AND text ILIKE '%robot%' ORDER BY content_id, node_index LIMIT 100;"
  },
  {
    "title": "Read a page outline",
    "description": "One row per HTML h1\u2013h6, including text inside nested elements. Heading levels come from tags, not visual styling.",
    "sql": "SELECT node_index, substr(tag, 2, 1)::INTEGER AS level, text\nFROM public_v1.html_element\nWHERE content_id = '01e3b8320926e10284e97da69093af4b4c04e181b5a3607c05bfd1920134a770'\n  AND namespace = 'http://www.w3.org/1999/xhtml'\n  AND tag IN ('h1', 'h2', 'h3', 'h4', 'h5', 'h6')\nORDER BY node_index;"
  },
  {
    "title": "Inspect image declarations",
    "description": "src and srcset are source strings, often relative. Missing alt is an empty string; an explicitly empty alt is an empty string. Dimensions are declared attributes, not measured pixels.",
    "sql": "SELECT node_index, attributes['src'] AS src, attributes['alt'] AS alt,\n       attributes['srcset'] AS srcset\nFROM public_v1.html_element\nWHERE content_id = '01e3b8320926e10284e97da69093af4b4c04e181b5a3607c05bfd1920134a770'\n  AND namespace = 'http://www.w3.org/1999/xhtml' AND tag = 'img'\nORDER BY node_index;"
  }
] as const
