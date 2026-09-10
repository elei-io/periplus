# Upstream feedback

Periplus uses the official DuckDB and DuckLake extensions directly. LakeDucktor owns physical lake
maintenance; Periplus owns ingestion evidence and logical materialization generations.

## DuckLake rejects column comments on views

- **Periplus caller:** public `web.*` and `content.*` catalogue documentation.
- **Evidence:** commenting on a view succeeds; commenting on one of its columns fails because the
  entry is not a table.
- **Needed upstream contract:** allow standard column comments on persistent DuckLake views and
  expose them through `duckdb_columns()`.
- **Periplus status:** Periplus publishes supported view comments and serves view-column descriptions
  from its authoritative manifest.

## DuckLake UUID bucket projection can crash during DML

- **Periplus caller:** superseded mutable material page and link projections.
- **Evidence:** DuckDB/DuckLake 1.5.5 on Linux ARM64 exits with `SIGSEGV` while DML writes a table
  partitioned by a UUID `bucket(64, ...)` transform. Captured native stacks include both
  `DuckLakeMergeInsert::Sink` and `DuckLakeMergeUpdate::Sink`, followed by
  `ProjectAndCastForCopy` → `Murmur3ScalarFunction` → `Vector::GetValueInternal`. A filtered
  `INSERT ... SELECT` can trigger the same invalid vector access. Isolated temporary DuckLakes
  reproduce exit 139 without Periplus state, CDC, NATS, Postgres, or LakeDucktor; equivalent
  unpartitioned writes succeed.
- **Needed upstream contract:** UUID bucket transforms must accept dictionary, selection, and
  projection vectors produced by DML without invalid vector access, with regression coverage for
  MERGE insert/update and filtered INSERT inputs.
- **Periplus status:** Periplus removed the affected projections. Semantic material data is immutable
  Parquet registered append-only; no page/head/link replacement DML remains.

## DuckLake Postgres inlined-data reads can invalidate independent readers

- **Periplus caller:** the serialized, process-owned API catalogue connection reading public and
  physical relations while independent ingestor connections commit DuckLake evidence.
- **Evidence:** DuckDB/DuckLake 1.5.5 on Linux ARM64 raised `INTERNAL Error: Attempted to access
  index 0 within vector of size 0` in
  `PostgresMetadataManager::TransformInlinedData` ->
  `DuckLakeInlinedDataReader::TryInitializeScan`. DuckDB then permanently rejected every query on
  that connection with `database has been invalidated`. At the same metadata snapshot, fresh
  attachments successfully read all four public views. The catalogue contained active Postgres
  inlined-data generations and had concurrent commits from independent writer attachments. This is
  consistent with the cross-attachment inlined-data cache invalidation gap tracked in
  [duckdb/ducklake#1305](https://github.com/duckdb/ducklake/issues/1305), although that report's
  surface error is a stale missing-table reference rather than this invalid vector access.
- **Needed upstream contract:** an independently attached reader must see one transactionally
  consistent inlined-data membership and schema after another attachment commits or flushes inline
  data. A metadata-cache miss or stale entry must refresh without an internal error or connection
  invalidation.
- **Periplus status:** the API discards a connection after DuckDB `InternalException` or
  `FatalException`, opens a validated fresh attachment, and retries its serialized read-only
  operation once. This bounds the incident but does not replace an upstream cache-coherence fix.

## SQLGlot DuckDB tokenizer misclassifies anonymous parameter casts

- **Periplus caller:** parameterized public queries and crawler corpus seed SQL.
- **Evidence:** with SQLGlot 30.12.0, DuckDB tokenization of `SELECT ?::UUID` produces one
  `QDCOLON` token for `?::` and parsing fails. DuckDB 1.5.5 executes that statement successfully
  with a bound UUID string. SQLGlot accepts `SELECT ? :: UUID`, `SELECT CAST(? AS UUID)`, and
  `SELECT $1::UUID`. The shared tokenizer keyword table supplies the conflicting operator.
- **Needed upstream contract:** DuckDB's dialect should tokenize adjacent `?::` as an anonymous
  parameter followed by a cast, with parameter, literal/comment, and surrounding-expression tests.
- **Periplus implementation:** the query-boundary DuckDB tokenizer omits this one inherited keyword.
  It does not rewrite SQL, alter global SQLGlot state, change parameters, or weaken namespace and
  statement checks. Regression tests execute both cast forms through a real read-only DuckLake
  query service and confirm the seed client sends the original SQL. No upstream issue or message
  has been published from this session.

## DuckLake INSERT RETURNING in administrative SQL

- **Periplus caller:** the privileged SQL console, executing operator-authored DuckDB SQL.
- **Evidence:** DuckDB v1.5.5 with official DuckLake extension `d8a1881e`, using a temporary
  local DuckLake: `CREATE TABLE sample (id BIGINT); INSERT INTO sample SELECT * FROM
  range(1200) RETURNING id` fails with `Binder Error: RETURNING clause not yet supported
  for insertion into DuckLake table`.
- **Needed upstream contract:** support DuckDB's INSERT RETURNING semantics for DuckLake,
  including transaction rollback and bounded client consumption of returned rows.
- **Periplus behavior:** raw SQL preserves the native operation and reports failure. The
  transaction/truncation regression instead uses an INSERT followed by SELECT in the same
  request. No emulation or SQL rewrite is introduced; no upstream issue has been published.

## Public query connection invalidation during dataset discovery

- **Periplus caller:** the public dataset builder reading book observations and bounded HTML subtrees.
- **Evidence (2026-09-08):** the long-lived query process on DuckDB 1.5.5 first logged
  `InvalidInputException`, then repeated `FatalException` for subsequent requests, including simple
  observation counts. Its unconditional health endpoint continued reporting success. A fresh
  read-only attachment to the same lake executed the earlier subtree query successfully and read
  the existing book data. The initiating engine error has not been reproduced on a fresh attachment;
  the observed exception types alone do not establish its upstream cause.
- **Needed upstream evidence:** capture a minimal reproducer and sanitized initial engine diagnostic
  if the invalidation recurs, including concurrent catalogue commits and extension versions.
- **Periplus behavior:** discard invalidated public-query handles, preserve the original error when
  rollback also fails, and reconnect with identical restrictions on the next request. The failed
  SQL is not replayed. Regression tests cover recovery, failed reconnect admission, and read-only
  restrictions after recovery. No query rewrite or schema workaround was introduced.

- **Further recurrence (2026-09-08 00:43 UTC):** request
  `3f49d97e-09b2-4e6b-ba07-f47186e33c01` failed in 243 ms with `query_failed`
  while inspecting three book-page families and up to four subtree roots per family.
  The same request through the existing process then returned `sql_invalid`; health
  remained 200. The exact SQL returned 10 rows on a fresh restricted attachment,
  including after replaying the preceding recorded queries. Restarting only the query
  process restored HTTP 200 and 10 rows through the public proxy. No fatal-handle
  discard was recorded. This is evidence of connection-dependent failure, not proof
  of its initiating cause or a schema/optimizer defect. The private query history
  retains the original SQL; server-side 5xx diagnostics now retain sanitized exception
  class and frame locations through SafeFormatter, without native messages or SQL.

- **Public v1 recurrence (2026-09-08 04:28:44 UTC):** request
  `e80a9c10-6c3e-4d1b-858e-f600090a12c5`, operation
  `4ae22c07-5b5d-4758-8368-e8a7ff6507c2`, aborted the process with
  `Attempted to access index 0 within vector of size 0`. Compose restarted it.
  The exact query below then succeeded (one row, h1 node 112), as did nine public
  examples. A fresh capture/materialization had committed around this time.
  This establishes a connection-dependent engine failure, not a public-grain
  problem; no optimizer plan evidence yet establishes an optimizer cause.
  Further upstream reproduction must include the preceding connection/catalogue
  history and concurrent commits. The source snapshot read shortly before/after
  the failure was 2118; the activated generation covers 587 original visits.

  ```sql
  SELECT e.tag, e.node_index, e.text_direct, n.node_type, t.text
  FROM html_element e
  JOIN html_node n USING (content_id, node_index),
  LATERAL subtree_text(e.content_id, e.node_index) t
  WHERE e.content_id =
    '9fe4eaf03c16535a08b508569fec826fbcb11ecd904abd24ce9419e8ba7ff5b7'
    AND e.tag = 'h1';
  ```

## Live DML consumer stalls at a schema-only boundary

On 2026-09-08, retiring ingest.visits.provenance left the live consumer repeatedly
reporting CDC_SCHEMA_BOUNDARY: its window ended at snapshot 4990/schema 25 and the
next snapshot was 5018/schema 28. Native visit insertion after the DDL committed,
but public query readiness remained materialization_pending. The current adapter
filters cdc_dml_changes_listen to inserts and returns no window when no rows are
returned; it cannot advance an empty schema-boundary window through that result.
The log alone does not establish whether the extension or adapter owns the defect.

The deployment uses the existing complete rebuild to activate a new generation
and start its consumer at the new source schema. No second cursor or compatibility
path was added. A coherent fix needs an explicit, testable empty-window checkpoint
contract across schema-only boundaries, including manual commit and filtered DML.

## Requested join keys arrive after grouped/windowed derived work

- **Caller:** selective prose discovery joined to metadata, headings or sections.
- **Evidence:** DuckDB 1.5.5 computes all 1,478 title groups for 289 selected content
  IDs; view-boundary semijoin and lateral wrappers do not reduce that work. Input
  semijoins reduce groups to 289 with identical result multiplicity. Section
  windows show the same pattern. A standalone accounts/events reproduction
  computes 1,000 groups for eight requested keys, versus eight with explicit
  input semijoins, without DuckLake or Periplus.
- **Reproduction and measurements:**
  [requested-key investigation](docs/query-investigations/key-domain/README.md)
  and [standalone SQL](docs/query-investigations/key-domain/reproduce.sql).
- **Needed investigation:** propagate demanded keys through group/partition keys
  and equivalent join bindings, preserving outer-join provenance and complete
  window partitions. The pinned join-filter pass traverses only the first join
  child and has no WINDOW case; title plans put the grouped key on the other
  input of an intervening RIGHT join. Evaluate both static semijoin reduction
  and dynamic-filter propagation; more selective computation does not itself
  establish file pruning or justify unconditional rewrites.
- **Periplus status:** read-only experiments only. No extra materialization,
  deployment change, optimizer extension, or production prep rewrite was added.
