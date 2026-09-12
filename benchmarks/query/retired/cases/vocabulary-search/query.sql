-- Public result-bound case. The supporting experiment also compares complete
-- internal candidate/reference sets before testing the bounded match pipeline.
SELECT content_id FROM public_v1.search('robot') ORDER BY content_id;
