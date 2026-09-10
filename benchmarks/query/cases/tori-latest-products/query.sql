WITH latest AS (
SELECT effective_url, content_id, captured_at,
row_number() OVER (PARTITION BY effective_url ORDER BY captured_at DESC, capture_id DESC) AS rn
FROM public_v1.capture
WHERE effective_url LIKE 'https://www.tori.fi/recommerce/forsale/item/%'
AND http_status_code = 200
), products AS (
SELECT l.effective_url AS listing_url, l.captured_at, j.value
FROM latest l
JOIN public_v1.html_jsonld j USING (content_id)
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
