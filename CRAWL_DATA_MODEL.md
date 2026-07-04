# Crawl Data Model Notes

This note captures the working design for making Atlas crawl knowledge durable and queryable.
It is intentionally a living design note, not a finished migration spec.

## Goal

Atlas should be able to answer operational and product questions from Postgres without
opening crawl artifact files:

- What is the scrape volume for this domain during the last hour?
- How many tasks are in flight for this URL pattern?
- How has the error profile for this URL path developed over time?
- Are scrapes from this URL looking healthy, or showing warning signs that we are pushing too much?
- Do we have recent artifacts for this URL that can be reused as cached responses?
- Which extraction schema version produced a task run's structured output?

The guiding split is:

- Postgres stores queryable facts, provenance, identities, timings, and warning metadata.
- Artifact storage stores large bytes such as HTML, screenshots, PDFs, and MHTML.

## Core Entities

### URLs

`urls` is the set of unique URLs known to Atlas.

Each row should contain the full URL plus derived query dimensions for analytics and lookup:

- `url`
- `normalized_url`
- `scheme`
- `host`
- `domain` or registrable domain
- `path`
- optional query fingerprint fields

URLs are not task output. They are stable dimensions used by crawls, artifacts, task matching,
cache lookup, and analytics.

Do not add `first_seen_at` or `last_seen_at` yet. If that data becomes necessary, it can be
derived from crawls with joins. Avoid write-time ceremony until there is a concrete caller.

### Crawls

`crawls` represents one point in time where Atlas actually visited a remote URL and loaded it.

A crawl spends browser/network budget. Reprocessing stored HTML with Crawl4AI `raw:` does not
create a new crawl.

Each row should capture visit facts:

- `url_id`
- `task_run_id`
- `started_at`
- `finished_at`
- `duration_ms`
- `inputs_json`
- `input_hash`
- `success`
- `status_code`
- `redirects_json`
- `errors_json`
- `retry_count`
- `warnings_json`
- `meta`

Warnings are non-fatal crawl-level signals, such as slow response time, transient retry success,
rate-limit symptoms, or suspicious redirects.

Crawls are transport records. Fields that describe how the remote visit happened belong here.
Use `inputs_json` rather than early real columns for evolving crawl inputs such as mode, wait,
headers, browser config, or Crawl4AI options. Use `errors_json` and `redirects_json` rather than
prematurely modeling every error or redirect shape.

Keep first-class Crawl4AI-derived columns minimal in the first pass. `success` and `status_code`
are likely enough, plus timing and relationship columns owned by Atlas. Everything else should
stay in JSONB until repeated queries justify promotion.

`meta` can hold transport-adjacent metadata such as response headers, cache status, console
messages, network request summaries, and crawl stats.

Do not store extracted content summaries such as links and media on crawls in the first pass.
Those facts describe the bytes that came out of the crawl and fit better on artifacts.

### Artifacts

`artifacts` represents bytes stored on disk or object storage.

Artifacts are mostly HTML and image-like outputs. JSON manifest artifacts should become less
important as queryable metadata moves into Postgres.

Each row should capture storage facts:

- `crawl_id`
- `url_id`
- `task_run_id`
- `kind` such as `html`, `screenshot`, `pdf`, `mhtml`
- `storage_uri` or local `path`
- `content_type`
- `size_bytes`
- `sha256`
- `input_hash`
- `extracted`
- `meta`
- `warnings_json`
- `created_at`
- `invalidated_at`
- `invalidated_reason`

Warnings are non-fatal artifact-level signals, such as empty HTML shells, known loading
elements, unexpectedly small files, or content hashes that indicate repeated blocked pages.

For the first pass, `artifact.extracted` should only contain content-derived summaries returned
by Crawl4AI:

```json
{
  "links": [],
  "media": []
}
```

Artifacts do not have TTL semantics yet. Later cleanup can decide which artifact bytes to delete.
For now, artifacts need quick invalidation so cache lookup can reject known-bad entries.

Artifact cleanup should physically remove cache entries older than
`.env` `ARTIFACT_CACHE_AGE_SECONDS`.

Invalidation is separate from cleanup. It should be possible to invalidate artifacts manually via
API, especially from an admin UI where an operator can browse artifacts grouped by URL/domain,
notice elevated warning counts, adjust crawl configuration, and invalidate cache entries for
selected URLs or URL groups.

### Extract Schemas

`extract_schemas` is the stable logical extraction intent.

An extract schema should not directly belong to one URL. The relationship between a schema and
URLs is ambiguous because schemas apply to page shapes, not exact URLs. The practical identity is
derived from extraction intent plus URL-derived shape signals.

Proposed identity:

```text
identity_key = hash(prompt + schema_type + target_shape + domain + path)
```

Use the URL path without query parameters. Include schema type and target shape because the same
prompt against different selector strategies or desired output shapes should not resolve to the
same logical schema.

Exact path is the v1 scope. The assumption is that the same path on a website usually has the
same HTML structure. Normalized path patterns, manually supplied patterns, or route fingerprints
can wait until exact path identity creates obvious duplication or misses.

- prompt or prompt hash
- schema type, such as CSS or XPath
- target JSON shape hash when present
- domain
- path excluding query parameters

Each row should capture:

- `identity_key`
- `prompt`
- `prompt_hash`
- `schema_type`
- `target_json_hash`
- `domain`
- `path`
- `current_version_id`
- `created_at`
- `updated_at`

### Extract Schema Versions

`extract_schema_versions` is the immutable executable extraction recipe.

Tasks may point at the logical schema, but task runs should record the exact version used.
This preserves reproducibility when the current schema changes later.

Each row should capture:

- `extract_schema_id`
- `version`
- `schema_json`
- `schema_hash`
- `generated_from_crawl_id`
- `generated_from_artifact_id`
- `generated_by_task_run_id`
- `inputs_json`
- model/provider settings used for generation
- validation status
- `warnings_json`
- `created_at`

## Relationships

```text
Url 1 -> many Crawls
Url 1 -> many Artifacts
Crawl 1 -> many Artifacts
TaskRun 1 -> many Crawls
TaskRun 1 -> many Artifacts
ExtractSchema 1 -> many ExtractSchemaVersions
Task many -> one ExtractSchema, optional
TaskRun many -> one ExtractSchemaVersion, optional
ExtractSchemaVersion many -> one generated-from Crawl, optional
ExtractSchemaVersion many -> one generated-from Artifact, optional
```

Cached reuse should be represented separately from production. A task run can use crawls or
artifacts produced by earlier task runs.

Candidate join tables:

```text
task_run_crawls
- task_run_id
- crawl_id
- role: produced, reused, input, cache_hit
- created_at

task_run_artifacts
- task_run_id
- artifact_id
- role: produced, reused, input, cache_hit
- created_at
```

## Single Crawl Chokepoint

Atlas should have one service boundary responsible for actual remote crawling.

That boundary should:

1. Resolve or create the `urls` row.
2. Execute Crawl4AI against the remote URL.
3. Measure timing and status data.
4. Run crawl-level warning checks.
5. Store the `crawls` row.
6. Store produced artifacts and artifact-level warnings.
7. Return enough in-memory data for the calling primitive.

Other actions should avoid directly visiting remote pages. If recent reusable HTML exists, they
can load the artifact bytes and run Crawl4AI with `raw:` to rebuild a `CrawlResult`-shaped object.
That reprocessing should be recorded as artifact reuse by the current task run, not as a new crawl.

Compute is cheap compared with browser time.

## Cache Semantics

Fresh remote crawl:

```text
TaskRun -> Crawl -> Artifact(html)
```

Cached HTML reuse:

```text
TaskRun -> task_run_artifacts(role=cache_hit) -> Artifact(html) -> Crawl
TaskRun reprocesses bytes through Crawl4AI raw:
```

The first cache policy should be deliberately simple and live behind the single crawl/artifact
lookup chokepoint:

1. Match by `url_id` and `input_hash`.
2. Require artifact exists.
3. Require `invalidated_at is null`.
4. Require no warnings, or only explicitly allowed warnings.

The cache key for an item is always:

```text
url + input config hash
```

More nuanced policy can later consider artifact age, URL/domain crawl volume, prior warning
codes, content hash, task primitive, and schema version requirements.

Manual invalidation should support URL-oriented workflows, such as invalidating artifacts for
one URL, a set of URLs, a domain, or a path group after crawl configuration changes.

## Task And Schema Pinning

Tasks can use schema references in two modes:

- Floating: use the current version of an `extract_schema`.
- Pinned: use a specific `extract_schema_version`.

Every task run should record the exact `extract_schema_version_id` it used, even when the task
itself is floating.

## Implementation Sequence

Suggested order:

1. Add URL models, schemas, and Alembic migration.
2. Expand artifacts to support `url_id`, `crawl_id`, `kind`, `size_bytes`, `sha256`,
   `content_type`, `input_hash`, `extracted`, `invalidated_at`,
   `invalidated_reason`, and `warnings_json`.
3. Add Crawl models, schemas, and migration.
4. Introduce the single crawl service and route existing `scrape` through it.
5. Add task-run usage joins for crawls and artifacts.
6. Add ExtractSchema and ExtractSchemaVersion models and migrations.
7. Route schema generation and extract execution through durable schema records.
8. Replace manifest-style artifact knowledge with Postgres-backed lookups.
9. Add analytics/query helpers for domain volume, in-flight URL patterns, warning trends,
   and recent reusable artifacts.

## Open Questions

No open modeling questions at this checkpoint.
