CREATE OR REPLACE VIEW public_v1.page AS
SELECT page_url AS url FROM public_v1.capture
UNION
SELECT effective_url AS url FROM public_v1.capture WHERE effective_url IS NOT NULL
UNION
SELECT target_url AS url FROM public_v1.link;
