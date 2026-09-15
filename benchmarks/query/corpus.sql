-- Run each statement separately through the query API.
SELECT capture_id, page_url, captured_at FROM public_v1.capture LIMIT 20;
SELECT tag, count() AS elements FROM public_v1.html_element GROUP BY tag ORDER BY elements DESC LIMIT 20;
SELECT capture_id, target_url FROM public_v1.link LIMIT 20;
SELECT url FROM public_v1.page LIMIT 20;
