-- Research only: run in a disposable DuckLake containing baseline.sql's view.
-- Keep the production catalogue read-only when copying the frozen input.
CREATE TABLE material.jsonld_plain AS SELECT * FROM public_v1.html_jsonld LIMIT 0;
INSERT INTO material.jsonld_plain SELECT * FROM public_v1.html_jsonld;

CREATE TABLE material.jsonld_sorted AS SELECT * FROM public_v1.html_jsonld LIMIT 0;
ALTER TABLE material.jsonld_sorted SET SORTED BY (content_id, node_index);
INSERT INTO material.jsonld_sorted SELECT * FROM public_v1.html_jsonld;

CREATE TABLE material.jsonld_bucket_sorted AS SELECT * FROM public_v1.html_jsonld LIMIT 0;
ALTER TABLE material.jsonld_bucket_sorted SET PARTITIONED BY (bucket(8, content_id));
ALTER TABLE material.jsonld_bucket_sorted SET SORTED BY (content_id, node_index);
INSERT INTO material.jsonld_bucket_sorted SELECT * FROM public_v1.html_jsonld;
