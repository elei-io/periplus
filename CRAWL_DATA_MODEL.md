# Crawl Data Model

This document is retained only as a pointer for old references. The former Postgres crawl,
observed-URL, and artifact model has been removed as a greenfield hard cut.

The authoritative design is [STORAGE.md](STORAGE.md):

- DuckLake owns durable `crawls`, `documents`, and `elements`.
- Filesystem or S3-compatible object storage owns content-addressed raw `.html.zst` objects.
- Postgres owns user-editable control-plane definitions, policies, schemas, and URL matches.
- JetStream is the target owner for temporal task/effect executions and ingestion jobs.

Do not recreate compatibility tables, routes, or artifact identifiers from the superseded model.
