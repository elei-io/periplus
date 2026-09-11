export const docsSqlPatterns = [
  {
    "title": "Discover pages by text",
    "description": "Up to 100 matching unique contents, with a representative capture URL. A page match does not imply that every element matches.",
    "sql": "SELECT * FROM search('robot') ORDER BY score DESC, content_id;"
  },
  {
    "title": "Find matching HTML headings",
    "description": "Complete descendant text preserves parsed whitespace and inline words. Script and style text are included when inside the selected element.",
    "sql": "SELECT content_id, node_index, tag, text FROM html_element WHERE tag = 'h1' AND text ILIKE '%robot%' ORDER BY content_id, node_index LIMIT 100;"
  },
  {
    "title": "Read a page outline",
    "description": "One row per HTML h1\u2013h6, including text inside nested elements. Heading levels come from tags, not visual styling.",
    "sql": "SELECT node_index, level, text\nFROM public_v1.html_heading\nWHERE content_id = '01e3b8320926e10284e97da69093af4b4c04e181b5a3607c05bfd1920134a770'\nORDER BY node_index;"
  },
  {
    "title": "Inspect table cells",
    "description": "Each source cell appears once. Zero-based grid positions and spans preserve merged cells; nested tables own their cells independently.",
    "sql": "SELECT t.node_index AS table_node_index, t.caption,\n       c.row_index, c.column_index, c.text,\n       c.is_header, c.row_span, c.column_span\nFROM public_v1.html_table t\nJOIN public_v1.html_table_cell c\n  ON c.content_id = t.content_id\n AND c.table_node_index = t.node_index\nWHERE t.content_id = '01e3b8320926e10284e97da69093af4b4c04e181b5a3607c05bfd1920134a770'\nORDER BY t.node_index, c.row_index, c.column_index;"
  },
  {
    "title": "Read titles, descriptions, and canonical declarations",
    "description": "Repeated declarations remain separate. Values and relative link URLs are preserved; select your own preferred declaration explicitly.",
    "sql": "SELECT node_index, kind, name, value\nFROM public_v1.html_metadata\nWHERE content_id = '01e3b8320926e10284e97da69093af4b4c04e181b5a3607c05bfd1920134a770'\n  AND (kind = 'title'\n       OR (kind = 'meta_name' AND lower(name) = 'description')\n       OR (kind = 'link_rel' AND lower(name) = 'canonical'))\nORDER BY node_index, kind, name;"
  },
  {
    "title": "Inspect image declarations",
    "description": "src and srcset are source strings, often relative. Missing alt is NULL; an explicitly empty alt is an empty string. Dimensions are declared attributes, not measured pixels.",
    "sql": "SELECT node_index, src, srcset, alt, width, height\nFROM public_v1.html_image\nWHERE content_id = '01e3b8320926e10284e97da69093af4b4c04e181b5a3607c05bfd1920134a770'\nORDER BY node_index;"
  },
  {
    "title": "Read a JSON-LD article",
    "description": "This captured article uses @graph. Inspect value before choosing a JSON path: other sites use objects or arrays. Parser failures remain rows with parse_error; there is no schema.org normalization.",
    "sql": "SELECT node_index,\n       (value -> '@graph') ->> '$[0].headline' AS headline,\n       parse_error\nFROM public_v1.html_jsonld\nWHERE content_id = '072a5a77e6bf9d591a30832addace1b20ccfa7e4e13b1bd9a00a3779b7bce252'\nORDER BY node_index;"
  },
  {
    "title": "Read list items in source order",
    "description": "item_index starts at zero. ordinal preserves ordered-list numbering, including start, reversed, and value resets; unordered items have no ordinal. Item text excludes nested lists.",
    "sql": "SELECT l.node_index AS list_node_index, l.ordered,\n       i.item_index, i.ordinal, i.text\nFROM public_v1.html_list l\nJOIN public_v1.html_list_item i\n  ON i.content_id = l.content_id\n AND i.list_node_index = l.node_index\nWHERE l.content_id = '01e3b8320926e10284e97da69093af4b4c04e181b5a3607c05bfd1920134a770'\nORDER BY l.node_index, i.item_index;"
  },
  {
    "title": "Inspect forms and their controls",
    "description": "Find search, signup, or checkout fields without submitting anything. LEFT JOIN retains empty forms; controls with no form owner are available by querying html_form_control directly. Flags describe own attributes, not inherited or live browser state.",
    "sql": "SELECT f.node_index AS form_node_index, f.action, f.method,\n       c.node_index AS control_node_index, c.tag, c.type,\n       c.name, c.required, c.disabled\nFROM public_v1.html_form f\nLEFT JOIN public_v1.html_form_control c\n  ON c.content_id = f.content_id\n AND c.form_node_index = f.node_index\nWHERE f.content_id =\n  '01c41839fd99a13cc60ada13d53a98fb5ad75fa65f70f64edae0acb5ee1eba61'\nORDER BY f.node_index, c.node_index;"
  },
  {
    "title": "Inspect language choices",
    "description": "Options join to their select control by content and node identity. value is the declared attribute, with no text fallback. selected and disabled reflect source attributes.",
    "sql": "SELECT c.node_index AS select_node_index, c.name,\n       o.option_index, o.value, o.text, o.selected, o.disabled\nFROM public_v1.html_form_control c\nJOIN public_v1.html_select_option o\n  ON o.content_id = c.content_id\n AND o.select_node_index = c.node_index\nWHERE c.content_id = '072a5a77e6bf9d591a30832addace1b20ccfa7e4e13b1bd9a00a3779b7bce252'\nORDER BY c.node_index, o.option_index;"
  },
  {
    "title": "Extract the passage under a heading",
    "description": "A section starts after its heading and ends at the next heading of equal or higher rank, or document end. Child sections overlap their parent. This extracts source text, including scripts/styles if present; it adds no separators or trimming.",
    "sql": "SELECT h.text AS heading,\n       coalesce(string_agg(n.value, '' ORDER BY n.node_index), '')\n         AS passage\nFROM public_v1.html_section s\nJOIN public_v1.html_heading h\n  ON h.content_id = s.content_id\n AND h.node_index = s.heading_node_index\nLEFT JOIN public_v1.html_node n\n  ON n.content_id = s.content_id\n AND n.node_index >= s.start_node_index\n AND n.node_index < s.end_node_index\n AND n.node_type = 'text'\nWHERE s.content_id = '01e3b8320926e10284e97da69093af4b4c04e181b5a3607c05bfd1920134a770'\n  AND h.text = 'Product Description'\nGROUP BY s.content_id, s.heading_node_index, h.text;"
  },
  {
    "title": "Find tables beneath Product Information",
    "description": "The same half-open section range can select tables, images, lists, or controls by their node position. Sections follow heading order, not HTML section elements; the final section can include footer content.",
    "sql": "SELECT h.text AS heading, t.node_index AS table_node_index, t.caption\nFROM public_v1.html_section s\nJOIN public_v1.html_heading h\n  ON h.content_id = s.content_id\n AND h.node_index = s.heading_node_index\nJOIN public_v1.html_table t\n  ON t.content_id = s.content_id\n AND t.node_index >= s.start_node_index\n AND t.node_index < s.end_node_index\nWHERE s.content_id = '01e3b8320926e10284e97da69093af4b4c04e181b5a3607c05bfd1920134a770'\n  AND h.text = 'Product Information'\nORDER BY t.node_index;"
  },
  {
    "title": "Inspect code snippets",
    "description": "One row per code element; block means it has a pre ancestor. Text preserves whitespace and nested syntax highlighting. Language hints remain available in html_element attributes.",
    "sql": "WITH pages AS (\n  SELECT DISTINCT content_id\n  FROM (\n    SELECT content_id FROM public_v1.capture\n    ORDER BY captured_at DESC NULLS LAST, capture_id DESC\n    LIMIT 10\n  )\n)\nSELECT c.content_id, c.node_index, c.block, c.text\nFROM public_v1.html_code c\nWHERE c.content_id IN (SELECT content_id FROM pages)\nORDER BY c.content_id, c.node_index\nLIMIT 100;"
  }
] as const

export const booksTableSql = `WITH books AS (
    SELECT effective_url AS url, captured_at, content_id
    FROM public_v1.capture
    WHERE effective_url LIKE 'https://books.toscrape.com/catalogue/%/index.html'
    QUALIFY row_number() OVER (
        PARTITION BY effective_url
        ORDER BY captured_at DESC NULLS LAST, capture_id DESC
    ) = 1
), fields AS (
    SELECT c.content_id, c.table_node_index, c.row_index,
           max(c.text) FILTER (WHERE c.column_index = 0) AS field,
           max(c.text) FILTER (WHERE c.column_index = 1) AS value
    FROM public_v1.html_table t
    JOIN public_v1.html_table_cell c
      ON c.content_id = t.content_id AND c.table_node_index = t.node_index
    WHERE t.content_id IN (SELECT content_id FROM books)
    GROUP BY c.content_id, c.table_node_index, c.row_index
), products AS (
    SELECT content_id, table_node_index,
           max(value) FILTER (WHERE field = 'UPC') AS upc,
           max(value) FILTER (WHERE field = 'Product Type') AS product_type,
           max(value) FILTER (WHERE field = 'Price (incl. tax)') AS price,
           max(value) FILTER (WHERE field = 'Availability') AS availability,
           max(value) FILTER (WHERE field = 'Number of reviews') AS review_count
    FROM fields
    GROUP BY content_id, table_node_index
    HAVING product_type = 'Books' AND upc IS NOT NULL
    QUALIFY row_number() OVER (
        PARTITION BY content_id ORDER BY table_node_index
    ) = 1
)
SELECT b.url, b.captured_at, p.upc,
       try_cast(replace(p.price, '£', '') AS DECIMAL(10, 2)) AS price_gbp,
       p.availability,
       try_cast(p.review_count AS INTEGER) AS review_count
FROM books b
JOIN products p USING (content_id)
ORDER BY b.url;`
