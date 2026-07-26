# Schema

Atlas separates observed evidence, Atlas-maintained derivations, the public query interface, and
user-owned data.

## `ingest.*`

Append-only records committed by acquisition:

- `ingest.crawls` — bounded executions of crawl graphs.
- `ingest.visits` — destinations observed during a crawl.
- `ingest.attempts` — acquisition attempts made for a visit.
- `ingest.steps` — ordered content-completion executions within an attempt.
- `ingest.documents` — references to immutable document bytes in object storage.

### `ingest.crawls`

One row is written when a crawl reaches its terminal state. Current execution state is not part of
the ingestion schema.

```text
crawl_id           # Unique identity of this crawl execution.
graph_id           # Stable logical identity of the crawl graph.
graph_config_hash  # Hash of the canonical frozen graph configuration.
graph_config       # Complete frozen graph configuration as JSON.
root_url_count     # Number of root URLs admitted to the initial frontier.
started_at         # Time at which crawl execution began.
finished_at        # Time at which crawl execution stopped.
stop_reason        # Reason the crawl stopped.
```

### `ingest.visits`

```text
visit_id          # Unique identity of this visit.
crawl_id          # Crawl that produced the visit.

requested_url     # Exact URL Atlas attempted to visit.
effective_url     # Final URL after navigation or redirects; null if never resolved.

admitted_at       # Time the destination entered the crawl.
started_at        # Time acquisition began.
observed_at       # Time the returned document was captured; null if none.
finished_at       # Time the visit reached its terminal outcome.

outcome           # Final logical result: succeeded, failed, cancelled, or another terminal state.
status_code       # Final HTTP status when available.
document_id       # Document produced by the visit; null if none.
```

Traversal and admission paths are operational state and are not retained here. The durable web
graph is derived from observed document content into `material.links`.

### `ingest.attempts`

```text
attempt_id       # Unique identity of this acquisition attempt.
visit_id         # Visit this attempt belongs to.
attempt_index    # Order of this attempt within the visit.

started_at       # Time acquisition work began.
finished_at      # Time the attempt reached its terminal outcome.

effective_url    # Final URL reached by this attempt; null if unresolved.
status_code      # HTTP status observed by this attempt; null if unavailable.

outcome          # Terminal result: succeeded, failed, or cancelled.
failure_stage    # Stage that failed; null on success.
failure_code     # Stable machine-readable failure reason; null on success.
failure_message  # Bounded diagnostic detail; null when unnecessary.
```

### `ingest.steps`

```text
attempt_id             # Attempt this step belongs to.
step_index             # Execution order within the attempt.

action                 # Controlled action name, such as wait_dynamic, wait_fixed, scroll, or expand.
parameters             # Complete frozen parameters for this execution as JSON.

started_at             # Time execution began.
duration_ms            # Total execution duration in milliseconds.
status                 # Execution result: succeeded, failed, or cancelled.
stopping_reason        # Why execution stopped; null when not applicable.

iterations             # Number of action iterations; null when not applicable.
matched_count          # Number of candidate targets found; null when not applicable.
applied_count          # Number of targets acted upon; null when not applicable.

before_content_hash    # Content fingerprint before execution; null when unavailable.
after_content_hash     # Content fingerprint after execution; null when unavailable.
before_html_bytes      # Serialized HTML size before execution; null when unavailable.
after_html_bytes       # Serialized HTML size after execution; null when unavailable.
before_element_count   # HTML element count before execution; null when unavailable.
after_element_count    # HTML element count after execution; null when unavailable.
before_text_chars      # Text character count before execution; null when unavailable.
after_text_chars       # Text character count after execution; null when unavailable.
before_document_height # Document height before execution; null when unavailable.
after_document_height  # Document height after execution; null when unavailable.

error_code             # Stable machine-readable failure reason; null on success.
error_message          # Bounded diagnostic detail; null when unnecessary.
```

### `ingest.documents`

A visit produces zero or one authoritative document. A document belongs to exactly one visit.

```text
document_id      # Unique identity of this document observation.
visit_id         # Visit that produced the document.
attempt_id       # Successful attempt that produced the document.

observed_at      # Time the document representation was captured.
representation   # Meaning of the bytes, such as response_body or rendered_html.

declared_media_type # Media type claimed by the source; null when unavailable.
detected_media_type # Media type determined by Atlas from the acquired representation.
charset             # Character encoding when meaningful; otherwise null.

content_sha256   # Hash of the uncompressed logical document bytes.
content_bytes    # Size of the uncompressed logical document bytes.

object_key       # Repository-relative pointer to the immutable bytes.
storage_encoding # Encoding used for the stored bytes.
stored_bytes     # Size of the stored object.
```

## `material.*`

Versioned, rebuildable relations maintained by Atlas:

- `material.html_elements` — structural projections of HTML documents.
- `material.xml_elements` — structural projections of XML documents.
- `material.json_values` — structural projections of JSON documents.
- `material.pdf_blocks` — structural projections of PDF documents.
- `material.pages` — visits reduced to unique page identities.
- `material.links` — observed links reduced by source and target URL.

Columns: TODO.

## `web.*`

The stable semantic interface implemented by the Atlas compiler:

- `web.crawls`
- `web.visits`
- `web.documents`
- `web.pages`
- `web.links`
- `web.dom`
- `web.xml`
- `web.json`
- `web.pdf`

Columns and exact interfaces: TODO.

## `data.*`

User-owned views, tables, and maintained extractions built primarily from `web.*`.

Tables are defined by the user.
