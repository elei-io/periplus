CREATE OR REPLACE MACRO public_v1.search(terms) AS TABLE
-- One word already has exactly one row per content; do not regroup its postings.
SELECT content_id, node_indexes, 1.0::DOUBLE AS score
FROM public_v1.html_term
WHERE len(terms::VARCHAR[]) = 1 AND term = (terms::VARCHAR[])[1]
UNION ALL
SELECT posting.content_id,
       list_sort(list_distinct(flatten(list(posting.node_indexes)))) AS node_indexes,
       count(*)::DOUBLE AS score
FROM public_v1.html_term AS posting
-- Fixed slots preserve literal term filters for DuckLake pruning. IN also
-- prevents duplicate query keys from multiplying postings or their score.
WHERE len(terms::VARCHAR[]) <> 1 AND CASE WHEN len(terms::VARCHAR[]) > 32
    THEN error('search accepts at most 32 term keys')
    ELSE posting.term IN (
        (terms::VARCHAR[])[1], (terms::VARCHAR[])[2], (terms::VARCHAR[])[3], (terms::VARCHAR[])[4], (terms::VARCHAR[])[5], (terms::VARCHAR[])[6], (terms::VARCHAR[])[7], (terms::VARCHAR[])[8],
        (terms::VARCHAR[])[9], (terms::VARCHAR[])[10], (terms::VARCHAR[])[11], (terms::VARCHAR[])[12], (terms::VARCHAR[])[13], (terms::VARCHAR[])[14], (terms::VARCHAR[])[15], (terms::VARCHAR[])[16],
        (terms::VARCHAR[])[17], (terms::VARCHAR[])[18], (terms::VARCHAR[])[19], (terms::VARCHAR[])[20], (terms::VARCHAR[])[21], (terms::VARCHAR[])[22], (terms::VARCHAR[])[23], (terms::VARCHAR[])[24],
        (terms::VARCHAR[])[25], (terms::VARCHAR[])[26], (terms::VARCHAR[])[27], (terms::VARCHAR[])[28], (terms::VARCHAR[])[29], (terms::VARCHAR[])[30], (terms::VARCHAR[])[31], (terms::VARCHAR[])[32], NULL) END
GROUP BY posting.content_id;
