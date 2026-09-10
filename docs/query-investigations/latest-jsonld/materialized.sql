WITH latest AS (
SELECT effective_url, content_id, captured_at,
row_number() OVER (PARTITION BY effective_url ORDER BY captured_at DESC, capture_id DESC) AS rn
FROM public_v1.capture
WHERE effective_url LIKE 'https://www.tori.fi/recommerce/forsale/item/%'
AND http_status_code = 200
), __selected AS MATERIALIZED (SELECT * FROM latest WHERE rn=1), __keys AS (SELECT DISTINCT content_id FROM __selected), __elements AS MATERIALIZED (SELECT e.* FROM public_v1.html_element e SEMI JOIN __keys USING(content_id)), __jsonld AS (WITH scripts AS (SELECT s.content_id, s.node_index, s.text_direct AS source_text FROM __elements AS s WHERE s.tag = 'script' AND s.namespace = 'http://www.w3.org/1999/xhtml' AND LOWER(TRIM(SPLIT_PART(s.attributes['type'], ';', 1), CHR(9) || CHR(10) || CHR(12) || CHR(13) || ' ')) = 'application/ld+json'), parsed AS (SELECT content_id, node_index, source_text, TRY_CAST(source_text AS JSON) AS value FROM scripts) SELECT content_id, node_index, value, CAST(CASE WHEN NOT value IS NULL THEN NULL WHEN TRIM(source_text, CHR(9) || CHR(10) || CHR(12) || CHR(13) || ' ') = '' THEN 'Empty JSON-LD script' ELSE 'Invalid JSON syntax' END AS TEXT) AS parse_error FROM parsed), products AS (
SELECT l.effective_url AS listing_url, l.captured_at, j.value
FROM __selected l
JOIN __jsonld j USING (content_id)
WHERE l.rn = 1
AND j.parse_error IS NULL
AND json_extract_string(j.value, '$."@type"') = 'Product'
)
SELECT
json_extract_string(value, '$.sku') AS listing_id,
json_extract_string(value, '$.name') AS name,
TRY_CAST(json_extract_string(value, '$.offers.price') AS DECIMAL(12,2)) AS price,
json_extract_string(value, '$.offers.priceCurrency') AS currency,
json_extract_string(value, '$.image') AS thumbnail,
json_extract_string(value, '$.additionalProperty[0].value') AS category,
CASE json_extract_string(value, '$.itemCondition')
WHEN 'UsedCondition' THEN 'used'
WHEN 'NewCondition' THEN 'new'
ELSE replace(json_extract_string(value, '$.itemCondition'), 'https://schema.org/', '')
END AS condition,
replace(json_extract_string(value, '$.offers.availability'), 'https://schema.org/', '') AS availability,
json_extract_string(value, '$.brand') AS brand,
listing_url,
captured_at
FROM products
WHERE json_extract_string(value, '$.name') IS NOT NULL
AND TRY_CAST(json_extract_string(value, '$.offers.price') AS DECIMAL(12,2)) IS NOT NULL
ORDER BY captured_at DESC
LIMIT 500;
