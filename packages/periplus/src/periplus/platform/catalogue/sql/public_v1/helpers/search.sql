-- Typed signature for catalogue discovery and DESCRIBE. Execution is owned by
-- the query API, which uses the pinned ICU tokenizer and the request snapshot.
CREATE OR REPLACE MACRO public_v1.search(query VARCHAR) AS TABLE (
    SELECT NULL::VARCHAR AS content_id,
           []::STRUCT(snippet VARCHAR, node_indexes INTEGER[])[] AS matches,
           NULL::DOUBLE AS score
    WHERE error('search() requires the Periplus query API')
);
