# Crawl graphs

Crawl graphs are editable Postgres definitions. A run freezes its complete graph configuration and
admits root URLs into durable crawl requests. Acquisition workers store immutable document bytes
before publishing visit evidence. Every terminal run schedules one `ingest.crawls` record through
the transactional graph outbox.

At this cutoff, executable SQL edges are unavailable because the Python compiler is archived and
the replacement C compiler has not yet been delivered. A working graph therefore consists of one
root acquisition node. Edge authoring and execution must not be used to prove setup,
ingestion, or materialization.

Current run/request/edge-evaluation state remains in Postgres and is never copied into DuckLake.
Historical evidence follows [`docs_v2/SCHEMA.md`](../docs_v2/SCHEMA.md).
