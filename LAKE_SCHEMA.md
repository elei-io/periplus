# Atlas Lake Schema

This document is the canonical contract for Atlas-owned physical tables in DuckLake. It defines
the initial lake schema, durable semantics, logical keys and relationships, physical partitioning
and sorting, and the comments that Atlas installs into DuckLake metadata.

Views, macros, materialization backing tables, and DuckLake's own metadata tables are outside this
contract.

## Contract principles

- Crawl evidence is immutable and self-describing.
- A normalized URL is stored with the observation that gives it meaning. There is no physical
  `urls` dimension table and no physical URL hash.
- `crawls` is the independently queryable logical acquisition result. Attempts and completion
  steps retain ordered supporting evidence.
- Documents and artifacts are immutable and content-addressed. Repeated captures may reference the
  same content identity.
- DuckLake supports `NOT NULL` but does not enforce primary keys, unique keys, foreign keys, or
  check constraints. Atlas enforces the logical contracts below through typed boundaries,
  operation leases, authoritative rereads, and atomic ingestion transactions.
- Partition and sort definitions are physical policy. They may evolve through an explicit schema
  contract revision without changing the meaning of existing rows.
- Every table and column has a DuckLake metadata comment installed with `COMMENT ON`.

## Logical keys and relationships

| Table | Logical key |
|---|---|
| `crawls` | `(crawl_id)` |
| `crawl_attempts` | `(crawl_id, attempt_number)` |
| `crawl_steps` | `(crawl_id, attempt_number, step_ordinal)` |
| `documents` | `(document_id)` |
| `elements` | `(document_id, element_index)` |
| `artifacts` | `(artifact_id)` |

Relationships:

```text
crawls.document_id                     -> documents.document_id
crawls.artifact_id                     -> artifacts.artifact_id
crawls.source_crawl_id                 -> crawls.crawl_id
crawl_attempts.crawl_id                -> crawls.crawl_id
crawl_steps.(crawl_id, attempt_number) -> crawl_attempts.(crawl_id, attempt_number)
elements.document_id                   -> documents.document_id
```

`document_id` and `artifact_id` use the algorithm-qualified content identity
`sha256:<lowercase hexadecimal digest>`.

## Physical layout

| Table | Partitioning | Sorting |
|---|---|---|
| `crawls` | `day(completed_at)` | `registrable_domain, host, path, completed_at, crawl_id` |
| `crawl_attempts` | `day(started_at)` | `crawl_id, attempt_number` |
| `crawl_steps` | `day(started_at)` | `crawl_id, attempt_number, step_ordinal` |
| `documents` | `bucket(64, document_id)` | `document_id` |
| `elements` | `bucket(64, document_id)` | `document_id, element_index` |
| `artifacts` | none | `artifact_id` |

The shared document bucket count is part of this physical contract. It keeps one document's
projection in one of a bounded number of partitions while limiting ingestion write fan-out.
Sorting inside the bucket provides finer file and row-group pruning.

## Canonical DDL

### `crawls`

One immutable logical acquisition result. It is the central crawl fact table and can be queried
without joining attempt evidence.

```sql
CREATE TABLE crawls (
    crawl_id UUID NOT NULL,
    document_id VARCHAR,
    artifact_id VARCHAR,
    graph_id UUID NOT NULL,
    graph_run_id UUID NOT NULL,
    graph_node_id UUID NOT NULL,
    source_crawl_id UUID,
    source_edge_id UUID,
    requested_url VARCHAR NOT NULL,
    url VARCHAR NOT NULL,
    scheme VARCHAR NOT NULL,
    host VARCHAR NOT NULL,
    port INTEGER NOT NULL,
    registrable_domain VARCHAR NOT NULL,
    path VARCHAR NOT NULL,
    query VARCHAR NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    content_captured_at TIMESTAMPTZ,
    status_code INTEGER,
    response_media_type VARCHAR,
    policy_schema_version INTEGER NOT NULL,
    effective_policy_hash VARCHAR NOT NULL,
    effective_policy JSON NOT NULL,
    outcome VARCHAR NOT NULL,
    failure_code VARCHAR,
    failure_stage VARCHAR,
    failure_retryable BOOLEAN,
    failure_detail VARCHAR
);

ALTER TABLE crawls SET PARTITIONED BY (day(completed_at));
ALTER TABLE crawls SET SORTED BY (
    registrable_domain ASC,
    host ASC,
    path ASC,
    completed_at ASC,
    crawl_id ASC
);

COMMENT ON TABLE crawls IS
    'Immutable logical page-acquisition results with self-describing effective URL, frozen policy, terminal outcome, and graph provenance.';
COMMENT ON COLUMN crawls.crawl_id IS
    'Stable logical crawl identity and the parent identity used by attempt and completion-step evidence.';
COMMENT ON COLUMN crawls.document_id IS
    'Content-addressed retained HTML document identity; null when the crawl retained no HTML document.';
COMMENT ON COLUMN crawls.artifact_id IS
    'Content-addressed retained non-HTML artifact identity; null when the crawl retained no artifact.';
COMMENT ON COLUMN crawls.graph_id IS
    'Identity of the crawl graph whose frozen run admitted this crawl.';
COMMENT ON COLUMN crawls.graph_run_id IS
    'Identity of the frozen graph run that admitted this crawl.';
COMMENT ON COLUMN crawls.graph_node_id IS
    'Identity of the graph node that acquired this URL.';
COMMENT ON COLUMN crawls.source_crawl_id IS
    'Prior crawl whose outgoing edge discovered this crawl, or null for a root crawl.';
COMMENT ON COLUMN crawls.source_edge_id IS
    'Graph edge that admitted this crawl, or null for a root crawl.';
COMMENT ON COLUMN crawls.requested_url IS
    'Normalized absolute HTTP or HTTPS URL Atlas was asked to acquire.';
COMMENT ON COLUMN crawls.url IS
    'Normalized effective URL after navigation; equals requested_url when no different final URL was observed.';
COMMENT ON COLUMN crawls.scheme IS
    'Lowercase scheme of the effective URL.';
COMMENT ON COLUMN crawls.host IS
    'Lowercase host of the effective URL.';
COMMENT ON COLUMN crawls.port IS
    'Effective URL port, including the default port derived from the scheme.';
COMMENT ON COLUMN crawls.registrable_domain IS
    'Registrable domain of the effective URL, or the host itself for an IP address or suffixless host.';
COMMENT ON COLUMN crawls.path IS
    'Normalized effective URL path, always beginning with a slash.';
COMMENT ON COLUMN crawls.query IS
    'Effective URL query without a leading question mark; empty when absent.';
COMMENT ON COLUMN crawls.started_at IS
    'Start time of the first acquisition attempt belonging to this logical crawl.';
COMMENT ON COLUMN crawls.completed_at IS
    'Terminal time of the logical crawl after success, skip, or failure.';
COMMENT ON COLUMN crawls.content_captured_at IS
    'Time retained response bytes were finalized; null when no document or artifact was captured.';
COMMENT ON COLUMN crawls.status_code IS
    'Terminal HTTP response status when one was obtained.';
COMMENT ON COLUMN crawls.response_media_type IS
    'Terminal accepted or observed response media type when one was obtained.';
COMMENT ON COLUMN crawls.policy_schema_version IS
    'Version of the durable effective_policy JSON contract.';
COMMENT ON COLUMN crawls.effective_policy_hash IS
    'Lowercase SHA-256 digest of the canonical effective_policy JSON representation.';
COMMENT ON COLUMN crawls.effective_policy IS
    'Complete frozen effective crawl and domain policy used by this crawl.';
COMMENT ON COLUMN crawls.outcome IS
    'Logical crawl outcome: success, skipped, or failed.';
COMMENT ON COLUMN crawls.failure_code IS
    'Stable typed terminal failure code; null unless outcome is failed.';
COMMENT ON COLUMN crawls.failure_stage IS
    'Stable acquisition stage where the terminal failure occurred; null unless outcome is failed.';
COMMENT ON COLUMN crawls.failure_retryable IS
    'Whether the terminal cause was classified as retryable when recorded; null unless outcome is failed.';
COMMENT ON COLUMN crawls.failure_detail IS
    'Bounded instance-specific terminal diagnostic; null unless outcome is failed.';
```

Invariants:

- `url` and its component columns describe the same normalized effective URL.
- `started_at <= completed_at`.
- `content_captured_at` is non-null exactly when `document_id` or `artifact_id` is non-null.
- A crawl never references both a document and an artifact.
- Successful crawls reference exactly one retained document or artifact.
- Failed crawls have all four failure provenance columns populated.
- Successful and skipped crawls have all four failure provenance columns null.
- `effective_policy_hash = sha256(canonical_json(effective_policy))`.

### `crawl_attempts`

Ordered network and navigation attempt evidence. Retry failures remain visible when a later attempt
succeeds.

```sql
CREATE TABLE crawl_attempts (
    crawl_id UUID NOT NULL,
    attempt_number INTEGER NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    requested_url VARCHAR NOT NULL,
    url VARCHAR NOT NULL,
    status_code INTEGER,
    response_media_type VARCHAR,
    outcome VARCHAR NOT NULL,
    failure_code VARCHAR,
    retry_after_seconds DOUBLE
);

ALTER TABLE crawl_attempts SET PARTITIONED BY (day(started_at));
ALTER TABLE crawl_attempts SET SORTED BY (
    crawl_id ASC,
    attempt_number ASC
);

COMMENT ON TABLE crawl_attempts IS
    'Ordered network and navigation attempt evidence for logical crawls, including retry failures that preceded terminal success or failure.';
COMMENT ON COLUMN crawl_attempts.crawl_id IS
    'Logical crawl identity that owns this attempt.';
COMMENT ON COLUMN crawl_attempts.attempt_number IS
    'One-based attempt ordinal within the logical crawl.';
COMMENT ON COLUMN crawl_attempts.started_at IS
    'Time this attempt began.';
COMMENT ON COLUMN crawl_attempts.completed_at IS
    'Time this attempt completed.';
COMMENT ON COLUMN crawl_attempts.requested_url IS
    'Normalized absolute HTTP or HTTPS URL used to begin this attempt.';
COMMENT ON COLUMN crawl_attempts.url IS
    'Normalized effective URL observed by this attempt, or requested_url when no different final URL was observed.';
COMMENT ON COLUMN crawl_attempts.status_code IS
    'HTTP response status obtained by this attempt, if any.';
COMMENT ON COLUMN crawl_attempts.response_media_type IS
    'Response media type observed by this attempt, if any.';
COMMENT ON COLUMN crawl_attempts.outcome IS
    'Attempt outcome: success, retry, skipped, or failed.';
COMMENT ON COLUMN crawl_attempts.failure_code IS
    'Stable typed attempt failure code, or null when the attempt did not fail.';
COMMENT ON COLUMN crawl_attempts.retry_after_seconds IS
    'Server-requested retry delay in seconds, or null when absent.';
```

Invariants:

- `(crawl_id, attempt_number)` is unique.
- Attempt numbers are contiguous and begin at one.
- `started_at <= completed_at`.

### `crawl_steps`

Ordered evidence for browser content-completion methods executed during an attempt.

```sql
CREATE TABLE crawl_steps (
    crawl_id UUID NOT NULL,
    attempt_number INTEGER NOT NULL,
    step_ordinal INTEGER NOT NULL,
    method VARCHAR NOT NULL,
    method_version INTEGER NOT NULL,
    config_hash VARCHAR NOT NULL,
    config_json JSON NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    duration_ms BIGINT NOT NULL,
    iterations INTEGER NOT NULL,
    stop_reason VARCHAR NOT NULL,
    before_element_count BIGINT NOT NULL,
    after_element_count BIGINT NOT NULL,
    before_text_chars BIGINT NOT NULL,
    after_text_chars BIGINT NOT NULL,
    before_link_count BIGINT NOT NULL,
    after_link_count BIGINT NOT NULL,
    before_scroll_height BIGINT NOT NULL,
    after_scroll_height BIGINT NOT NULL
);

ALTER TABLE crawl_steps SET PARTITIONED BY (day(started_at));
ALTER TABLE crawl_steps SET SORTED BY (
    crawl_id ASC,
    attempt_number ASC,
    step_ordinal ASC
);

COMMENT ON TABLE crawl_steps IS
    'Ordered per-attempt evidence for dynamic waiting, fixed waiting, scrolling, and expansion content-completion methods.';
COMMENT ON COLUMN crawl_steps.crawl_id IS
    'Logical crawl identity that owns this completion step.';
COMMENT ON COLUMN crawl_steps.attempt_number IS
    'One-based parent attempt ordinal.';
COMMENT ON COLUMN crawl_steps.step_ordinal IS
    'One-based completion-step ordinal within the attempt.';
COMMENT ON COLUMN crawl_steps.method IS
    'Completion method: wait_dynamic, wait_fixed, scroll, or expand.';
COMMENT ON COLUMN crawl_steps.method_version IS
    'Version of the completion-method evidence contract.';
COMMENT ON COLUMN crawl_steps.config_hash IS
    'Lowercase SHA-256 digest of the canonical config_json representation.';
COMMENT ON COLUMN crawl_steps.config_json IS
    'Frozen effective configuration used for this completion step.';
COMMENT ON COLUMN crawl_steps.started_at IS
    'Time this completion step began.';
COMMENT ON COLUMN crawl_steps.duration_ms IS
    'Elapsed completion-step duration in milliseconds.';
COMMENT ON COLUMN crawl_steps.iterations IS
    'Number of bounded method iterations performed.';
COMMENT ON COLUMN crawl_steps.stop_reason IS
    'Stable reason the completion method stopped.';
COMMENT ON COLUMN crawl_steps.before_element_count IS
    'DOM element count observed before the completion step.';
COMMENT ON COLUMN crawl_steps.after_element_count IS
    'DOM element count observed after the completion step.';
COMMENT ON COLUMN crawl_steps.before_text_chars IS
    'DOM text character count observed before the completion step.';
COMMENT ON COLUMN crawl_steps.after_text_chars IS
    'DOM text character count observed after the completion step.';
COMMENT ON COLUMN crawl_steps.before_link_count IS
    'HTTP or HTTPS anchor count observed before the completion step.';
COMMENT ON COLUMN crawl_steps.after_link_count IS
    'HTTP or HTTPS anchor count observed after the completion step.';
COMMENT ON COLUMN crawl_steps.before_scroll_height IS
    'Document scroll height observed before the completion step.';
COMMENT ON COLUMN crawl_steps.after_scroll_height IS
    'Document scroll height observed after the completion step.';
```

Invariants:

- `(crawl_id, attempt_number, step_ordinal)` is unique.
- Step ordinals are contiguous within an attempt and begin at one.
- `config_hash = sha256(canonical_json(config_json))`.

### `documents`

One immutable content-addressed HTML document and the active structural projection recipe committed
with it.

```sql
CREATE TABLE documents (
    document_id VARCHAR NOT NULL,
    object_key VARCHAR NOT NULL,
    content_type VARCHAR NOT NULL,
    encoding VARCHAR NOT NULL,
    size_bytes BIGINT NOT NULL,
    compressed_size_bytes BIGINT NOT NULL,
    compression VARCHAR NOT NULL,
    dom_schema_version INTEGER NOT NULL,
    parser_name VARCHAR NOT NULL,
    parser_version VARCHAR NOT NULL,
    parser_options_hash VARCHAR NOT NULL,
    element_count BIGINT NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL
);

ALTER TABLE documents SET PARTITIONED BY (bucket(64, document_id));
ALTER TABLE documents SET SORTED BY (document_id ASC);

COMMENT ON TABLE documents IS
    'Immutable content-addressed retained HTML documents and their active structural DOM projection recipe.';
COMMENT ON COLUMN documents.document_id IS
    'Algorithm-qualified content identity in sha256:<lowercase hexadecimal digest> form.';
COMMENT ON COLUMN documents.object_key IS
    'Repository-relative key of the immutable compressed HTML object.';
COMMENT ON COLUMN documents.content_type IS
    'Stored HTML or XHTML media type.';
COMMENT ON COLUMN documents.encoding IS
    'Character encoding used for the canonical retained HTML text.';
COMMENT ON COLUMN documents.size_bytes IS
    'Uncompressed canonical HTML size in bytes.';
COMMENT ON COLUMN documents.compressed_size_bytes IS
    'Compressed immutable object size in bytes.';
COMMENT ON COLUMN documents.compression IS
    'Compression format used by the immutable repository object.';
COMMENT ON COLUMN documents.dom_schema_version IS
    'Version of the elements table structural projection contract.';
COMMENT ON COLUMN documents.parser_name IS
    'Parser implementation used for the active structural projection.';
COMMENT ON COLUMN documents.parser_version IS
    'Parser implementation version used for the active structural projection.';
COMMENT ON COLUMN documents.parser_options_hash IS
    'Lowercase SHA-256 digest of the canonical parser options.';
COMMENT ON COLUMN documents.element_count IS
    'Expected number of elements rows in the active structural projection.';
COMMENT ON COLUMN documents.first_seen_at IS
    'Time this content identity was first committed to DuckLake.';
```

Invariants:

- `document_id` equals the SHA-256 identity of the canonical uncompressed UTF-8 HTML bytes.
- One current document row exists per `document_id`.
- Exactly `element_count` current `elements` rows exist for the document.

### `elements`

The complete versioned structural DOM projection. Element order is depth-first document order.

```sql
CREATE TABLE elements (
    document_id VARCHAR NOT NULL,
    element_index INTEGER NOT NULL,
    parent_index INTEGER,
    subtree_end_index INTEGER NOT NULL,
    depth INTEGER NOT NULL,
    tag VARCHAR NOT NULL,
    namespace_uri VARCHAR,
    attributes MAP(VARCHAR, VARCHAR) NOT NULL,
    text_direct VARCHAR NOT NULL,
    text_tail VARCHAR NOT NULL
);

ALTER TABLE elements SET PARTITIONED BY (bucket(64, document_id));
ALTER TABLE elements SET SORTED BY (
    document_id ASC,
    element_index ASC
);

COMMENT ON TABLE elements IS
    'Versioned structural DOM projection stored in depth-first document order and bucketed by document identity.';
COMMENT ON COLUMN elements.document_id IS
    'Content-addressed parent document identity.';
COMMENT ON COLUMN elements.element_index IS
    'Zero-based depth-first element ordinal within the document.';
COMMENT ON COLUMN elements.parent_index IS
    'Element index of the parent element, or null for the document element.';
COMMENT ON COLUMN elements.subtree_end_index IS
    'Inclusive final element index in this element subtree.';
COMMENT ON COLUMN elements.depth IS
    'Zero-based element depth, with the document element at depth zero.';
COMMENT ON COLUMN elements.tag IS
    'Normalized element tag name.';
COMMENT ON COLUMN elements.namespace_uri IS
    'Element namespace URI, or null when absent.';
COMMENT ON COLUMN elements.attributes IS
    'Map of parsed element attribute names to values.';
COMMENT ON COLUMN elements.text_direct IS
    'Character data directly inside the element before its first child.';
COMMENT ON COLUMN elements.text_tail IS
    'Character data immediately following the element in its parent.';
```

Invariants:

- `(document_id, element_index)` is unique.
- Element indexes are contiguous from zero through `documents.element_count - 1`.
- `element_index <= subtree_end_index`.
- `parent_index < element_index` when `parent_index` is non-null.
- All rows for one document use the same DOM schema and parser recipe recorded by `documents`.

### `artifacts`

One immutable content-addressed accepted response that is not represented as an HTML document.

```sql
CREATE TABLE artifacts (
    artifact_id VARCHAR NOT NULL,
    object_key VARCHAR NOT NULL,
    size_bytes BIGINT NOT NULL,
    response_media_type VARCHAR NOT NULL,
    detected_media_type VARCHAR NOT NULL,
    detector_name VARCHAR NOT NULL,
    detector_version VARCHAR NOT NULL,
    detection_confidence DOUBLE NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL
);

ALTER TABLE artifacts SET SORTED BY (artifact_id ASC);

COMMENT ON TABLE artifacts IS
    'Immutable content-addressed retained non-HTML response artifacts with media-type detection evidence.';
COMMENT ON COLUMN artifacts.artifact_id IS
    'Algorithm-qualified content identity in sha256:<lowercase hexadecimal digest> form.';
COMMENT ON COLUMN artifacts.object_key IS
    'Repository-relative key of the immutable artifact object.';
COMMENT ON COLUMN artifacts.size_bytes IS
    'Immutable artifact size in bytes.';
COMMENT ON COLUMN artifacts.response_media_type IS
    'Response media type declared by the acquisition response.';
COMMENT ON COLUMN artifacts.detected_media_type IS
    'Media type detected from the retained artifact bytes.';
COMMENT ON COLUMN artifacts.detector_name IS
    'Media-type detector implementation.';
COMMENT ON COLUMN artifacts.detector_version IS
    'Media-type detector implementation version.';
COMMENT ON COLUMN artifacts.detection_confidence IS
    'Detector confidence from zero through one.';
COMMENT ON COLUMN artifacts.first_seen_at IS
    'Time this content identity was first committed to DuckLake.';
```

Invariants:

- `artifact_id` equals the SHA-256 identity of the exact retained artifact bytes.
- One current artifact row exists per `artifact_id`.
- `0 <= detection_confidence <= 1`.

## Compiler metadata contract

Atlas compiler metadata must describe, for every physical table:

- stable logical key columns;
- logical relationships to other managed tables;
- nullability;
- partition expressions, including bucket counts;
- sort expressions and directions;
- table UUID and schema version;
- estimated rows, bytes, and file count when available;
- column distinct counts, null counts, and min/max statistics when available;
- every physical partition layout still visible in the selected DuckLake snapshot.

These facts permit semantics-preserving predicate propagation, partition-pruning estimates,
functional-dependency reasoning, incremental materialization proofs, and scan diagnostics while
users continue to author ordinary DuckDB-compatible SQL.

Physical ordering never supplies SQL result ordering. Atlas must retain every semantically required
`ORDER BY`.

## Contract versioning

The initial contract version is `1.0.0`.

Any change to table names, column names, column types, nullability, durable field semantics, logical
keys, or logical relationships requires an explicit version change in this document and in the
repository schema constant. Physical partition and sort changes must also be recorded here even
when DuckLake can evolve them without rewriting older files.
