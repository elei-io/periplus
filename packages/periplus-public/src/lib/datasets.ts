import type { Dataset } from "../types/dataset"

export const coverageSql = `WITH sites AS (
  SELECT split_part(requested_url, '/', 3) AS site,
         count(DISTINCT requested_url) AS pages,
         count(*) AS captures,
         count(*) FILTER (WHERE captured_at IS NULL) AS undated_captures,
         min(captured_at) AS first_captured,
         max(captured_at) AS last_captured
  FROM public_v1.capture
  GROUP BY site
)
SELECT *, count(*) OVER () AS total_sites,
       sum(pages) OVER () AS total_pages,
       sum(captures) OVER () AS total_captures
FROM sites
ORDER BY pages DESC, site
LIMIT 100;`

export const datasets: Dataset[] = [
  {
    slug: "books-with-prices",
    name: "Books with prices",
    category: "Product research · practice data",
    description: "See book pages as a price comparison. Choose titles, prices, and sources from their shared HTML structure, then adapt the query to your question.",
    grain: "One row per matching title–price pair, with its product URL.",
    scope: "Books to Scrape is a practice website with fictional commercial data. This query uses the latest dated capture for up to 20 collected product URLs. Prices reflect those captures, not a live shop or a complete catalogue.",
    sql: `WITH pages AS (
  SELECT requested_url, captured_at, content_id
  FROM public_v1.capture
  WHERE split_part(requested_url, '/', 3) = 'books.toscrape.com'
    AND requested_url LIKE '%/catalogue/%/index.html'
    AND requested_url NOT LIKE '%/category/%'
    AND content_id IS NOT NULL
  QUALIFY row_number() OVER (
    PARTITION BY requested_url
    ORDER BY captured_at DESC NULLS LAST, capture_id DESC
  ) = 1
  ORDER BY captured_at DESC NULLS LAST, requested_url
  LIMIT 20
)
SELECT title.text_direct AS title,
       try_cast(regexp_extract(price.text_direct, '[0-9]+[.][0-9]+')
         AS DECIMAL(10, 2)) AS price_gbp,
       pages.requested_url AS source_url,
       pages.captured_at AS collected_at
FROM pages
JOIN public_v1.html_element title USING (content_id)
JOIN public_v1.html_element price
  ON price.content_id = title.content_id
 AND price.parent_index = title.parent_index
WHERE title.tag = 'h1'
  AND price.tag = 'p'
  AND price.attributes['class'] = 'price_color'
ORDER BY price_gbp, source_url
LIMIT 20;`,
  },
  {
    slug: "page-heading-index",
    name: "Page heading index",
    category: "Content research",
    description: "See pages through their headings. Build a source-linked index to find topic mentions or compare how pages describe themselves.",
    grain: "One row per h1 element in the selected pages; a page may have several.",
    scope: "Uses the latest dated capture for up to 20 distinct URLs. Returns up to 100 headings. Direct text excludes text inside child elements; pages without matching HTML or h1 elements are absent.",
    sql: `WITH pages AS (
  SELECT requested_url, captured_at, content_id
  FROM public_v1.capture
  WHERE content_id IS NOT NULL
  QUALIFY row_number() OVER (
    PARTITION BY requested_url
    ORDER BY captured_at DESC NULLS LAST, capture_id DESC
  ) = 1
  ORDER BY captured_at DESC NULLS LAST, requested_url
  LIMIT 20
)
SELECT h.text_direct AS heading, pages.requested_url AS source_url,
       pages.captured_at AS collected_at
FROM pages JOIN public_v1.html_element h USING (content_id)
WHERE h.tag = 'h1' AND trim(h.text_direct) <> ''
ORDER BY source_url, h.node_index
LIMIT 100;`,
  },
  {
    slug: "linked-destinations",
    name: "Most-linked destinations",
    category: "Link analysis",
    description: "See the connections between pages. Find shared destinations and explore the link structure within the current corpus.",
    grain: "One row per target URL, ranked by distinct referring page URLs.",
    scope: "Aggregates links from the latest dated capture of each collected URL. Includes internal and external destinations. A link does not mean its target was collected, and distinct links do not establish endorsement or importance across the whole web.",
    sql: `WITH latest AS (
  SELECT capture_id, effective_url, captured_at
  FROM public_v1.capture
  QUALIFY row_number() OVER (
    PARTITION BY requested_url
    ORDER BY captured_at DESC NULLS LAST, capture_id DESC
  ) = 1
)
SELECT links.resolved_url,
       count(DISTINCT latest.effective_url) AS referring_pages,
       count(*) AS link_occurrences,
       max(latest.captured_at) AS last_captured
FROM public_v1.link_occurrence links
JOIN latest USING (capture_id)
GROUP BY links.resolved_url
ORDER BY referring_pages DESC, links.resolved_url
LIMIT 20;`,
  },
  {
    slug: "collection-freshness",
    name: "Collection freshness by site",
    category: "Dataset quality",
    description: "Check which sites are represented, how many URLs were observed, and where collection dates are missing before starting an analysis.",
    grain: "One row per requested-URL hostname, with retained content.",
    scope: "Aggregates all available captures and shows the first 100 sites by distinct URL count. Pages means distinct requested URLs; captures counts repeated captures with retained content. The newest timestamp is one capture, not a freshness guarantee for the whole site.",
    sql: coverageSql,
  },
]

export const featuredDataset = datasets[0]
export const datasetSqlUrl = (dataset: Pick<Dataset, "sql">) => `/sql?${new URLSearchParams({ sql: dataset.sql })}`
