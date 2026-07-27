# External HTML ingestion

Atlas accepts HTML acquired outside its crawler through one generic evidence boundary. External
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

Atlas stores the exact bytes before publishing ordinary ingestion evidence. It derives stable local
identities from the external source identity, so submitting the same record and evidence again is
idempotent. Conflicting reuse of an identity is rejected. Atlas does not invent acquisition
attempts, HTTP statuses, or navigation timestamps.

The supplied charset is retained as source evidence. Structural HTML materialization remains
content-addressed, so imported byte representations are parsed deterministically using HTML5 byte
encoding detection.

The API is intentionally source-neutral. A web-archive reader, another Atlas deployment, a local
exporter, or any other client can use it without adding provider-specific state or execution
machinery to Atlas.
