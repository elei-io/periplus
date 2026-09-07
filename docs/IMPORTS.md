# External HTML ingestion

Periplus accepts HTML acquired outside its crawler through one generic evidence boundary. External
HTML uses the same immutable object repository, ingestion queue, DuckLake relations, CDC, and fixed
materializations as native crawl evidence.

## HTTP API

`POST /ingest/html` accepts multipart form data:

- `content` is the exact HTML response body.
- `metadata` is JSON with:
  - `source_record_id`: stable identity assigned by the source.
  - `system`: source system name.
  - `dataset`: optional source dataset or export identity.
  - `requested_url`: URL associated with the HTML.
  - `observed_at`: timezone-aware source observation time.
  - `effective_url`, `status_code`, `declared_media_type`, and `charset`: optional source facts.

Periplus stores the exact bytes before publishing ordinary ingestion evidence. It derives stable local
observation identities from the canonical JSON tuple `[system, dataset, source_record_id]`, so submitting the same record and evidence again is
idempotent. Conflicting reuse of an identity is rejected. Periplus does not invent acquisition
attempts, HTTP statuses, or navigation timestamps.

The supplied charset is retained as source evidence. Structural HTML materialization remains
content-addressed, so imported byte representations are parsed deterministically using HTML5 byte
encoding detection.

The API is intentionally source-neutral. A web-archive reader, another Periplus deployment, a local
exporter, or any other client can use it without adding provider-specific state or execution
machinery to Periplus.

The response identifies `visit_id`, `document_id`, and retained content. An import publishes one
visit evidence job; it creates no synthetic collection, graph run, acquisition reason, or physical
attempt. Source provenance stays on the observation and remains available through public SQL.
