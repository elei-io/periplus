# Crawl Data Model Notes

This note captures the working design for making Atlas crawl knowledge durable and queryable.
It is intentionally a living design note, not a finished migration spec.

## Goal

Atlas should be able to answer operational and product questions from Postgres without
opening crawl artifact files:

- What is the crawl volume for this domain during the last hour?
- How many tasks are in flight for this URL pattern?
- How has the error profile for this URL path developed over time?
- Are crawls from this URL looking healthy, or showing warning signs that we are pushing too much?
- Do we have recent artifacts for this URL that can be reused as cached responses?
- Which extraction schema produced a task run's structured output?

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
- `query_fingerprint`, nullable

URLs are not task output. They are stable dimensions used by crawls, artifacts, task matching,
cache lookup, and analytics.

Do not add `first_seen_at` or `last_seen_at` yet. If that data becomes necessary, it can be
derived from crawls with joins. Avoid write-time ceremony until there is a concrete caller.

### Crawls

`crawls` represents one point in time where Atlas actually visited a remote URL and loaded it.

A crawl spends browser/network budget. Reprocessing stored HTML with Crawl4AI `raw:` does not
create a new crawl.

`actions.crawl` is the acquisition chokepoint. CLI, API, scheduled runs, and higher-level
task-backed actions that need page bytes should execute through the task-run path and pass their
task-run context into crawl. That one path is responsible for resolving URL rows, inserting crawl
rows, writing crawl-produced artifacts, and linking task runs to the crawls/artifacts they used or
produced.

Before spending browser time, crawl should look for a reusable HTML artifact keyed by URL and
input configuration hash. The first cache eligibility rule is intentionally simple: the HTML
artifact must exist, be present on disk, be linked to a crawl row, not be invalidated, and have no
artifact warnings. A cache hit links the prior crawl and HTML artifact to the current task run as
`used`; it does not insert a new `crawls` row.

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
Those facts describe the bytes that came out of the crawl and should be recomputed from stored
HTML when needed.

### Artifacts

`artifacts` represents bytes stored on disk or object storage.

Artifacts are mostly HTML and image-like outputs. JSON manifest artifacts should become less
important as queryable metadata moves into Postgres.

Each row should capture storage facts:

- `crawl_id`, nullable
- `url_id`, nullable
- `task_run_id`, nullable
- `kind` such as `html`, `screenshot`, `pdf`, `mhtml`
- `storage_uri` or local `path`
- `content_type`
- `size_bytes`
- `sha256`
- `input_hash`
- `meta`
- `warnings_json`
- `created_at`
- `invalidated_at`
- `invalidated_reason`

Warnings are non-fatal artifact-level signals, such as empty HTML shells, known loading
elements, unexpectedly small files, or content hashes that indicate repeated blocked pages.

Crawl HTML artifacts should not store Crawl4AI-derived `links` or `media` in Postgres. Those are
derived from the HTML bytes and should be recomputed on cache hit by passing the stored HTML back
through Crawl4AI with `raw:`. Do not keep an `artifact.extracted` column for this.

Artifact invalidation is the only place cache-death decisions should happen. Manual admin actions,
TTL invalidation via `ARTIFACT_CACHE_AGE_SECONDS`, and later policy-based invalidation all set
`invalidated_at` and `invalidated_reason`.

Artifact cleanup is purely mechanical. It only walks already-invalidated artifact rows, deletes
their bytes, and deletes the rows. It does not decide freshness, warning tolerance, TTL, or policy.
Analytics that need longer-term aggregate history should be scraped separately, for example by
Prometheus later.

The worker runs artifact cache maintenance automatically when
`ARTIFACTS_CLEANUP_INTERVAL_SECONDS` is greater than zero. Each pass first invalidates byte
artifacts older than `ARTIFACT_CACHE_AGE_SECONDS`, then deletes already-invalidated artifacts in
bounded batches controlled by `ARTIFACTS_CLEANUP_BATCH_SIZE`.

Invalidation is separate from cleanup. It should be possible to invalidate artifacts manually via
API, especially from an admin UI where an operator can browse artifacts grouped by URL/domain,
notice elevated warning counts, adjust crawl configuration, and invalidate cache entries for
selected URLs or URL groups.

### Extract Schemas

`extract_schemas` is a mutable cache of Crawl4AI extraction schemas for a page shape and
extraction intent.

An extract schema should not directly belong to one URL. The relationship between a schema and
URLs is ambiguous because schemas apply to page shapes, not exact URLs. The practical relationship
should be an explicit URL match pattern, using the same kind of glob-style patterns that already
work well for index filters.

Proposed identity:

```text
identity_key = hash(prompt + schema_type + target_shape + match)
```

`match` is a human-editable URL pattern, such as:

```text
*example.com/item/*
https://example.com/search*
```

This is the durable control surface. Automatic path normalization is too much of a hidden
heuristic and will eventually be wrong in ways that are hard to inspect. A match pattern can be
shown in the admin UI, edited, merged, widened, or narrowed by an operator.

Initial schema creation should use the exact URL with query parameters stripped as the first
`match` value. That is conservative and avoids accidental over-sharing. Later, an admin can merge
schemas and choose a broader match.

Example:

```text
https://example.com/item/00001
https://example.com/item/10000
```

These may start as two exact schema matches. The admin UI can show created schemas grouped by
domain/path similarity, allow selecting them for merge, and then ask for the replacement match,
for example:

```text
*example.com/item/*
```

Later, Atlas can recommend merges when several similar schemas are created, but the first design
should keep the final match pattern explicit.

- prompt or prompt hash
- schema type, such as CSS or XPath
- target JSON shape hash when present
- match pattern

Each row should capture:

- `identity_key`
- `match`
- `enabled`
- `priority`
- `prompt`
- `prompt_hash`
- `schema_type`
- `target_json_hash`
- `domain`
- `path`
- `schema_json`
- `schema_hash`
- `generated_from_crawl_id`
- `generated_from_artifact_id`
- `generated_by_task_run_id`
- `inputs_json`
- model/provider settings used for generation
- validation status
- `failure_count`
- `last_failed_at`
- `last_error`
- `warnings_json`
- `created_at`
- `updated_at`

Atlas does not need extraction schema versioning in the first design. Extraction is a
convenience feature for structured outputs and navigational effects, not a durable parsing
workload where historical replay matters. Web pages are not stable enough for replay to be a
trustworthy recovery strategy anyway.

Instead, extraction schemas should be self-healing:

1. Find enabled schemas whose `match` pattern matches the query-stripped URL and whose prompt,
   schema type, and target shape match the extraction intent.
2. If no matching schema exists, generate one with an exact query-stripped URL match and save it.
3. If a schema exists, use it.
4. If extraction fails, regenerate and replace the stored schema.
5. Retry replacement up to `.env` `MAX_EXTRACT_ATTEMPTS`.
6. If extraction still fails, mark the task run failed.

When multiple schemas match, choose by explicit `priority`, then by most-specific match as a tie
breaker. `identity_key` should enforce uniqueness for a specific
`prompt + schema_type + target_shape + match` combination, but match resolution is the lookup
mechanism.

Schema replacement should overwrite the current `schema_json` and update generation metadata.
If we later need an audit trail, that can be added separately as lightweight events, not as a
core versioning model.

### Web Search Providers

Web search providers are code-owned options for classic web search engines such as DuckDuckGo,
Brave, and Yahoo.

They are not stored in Postgres. Atlas keeps a small known-working provider registry in code and
the web search playground exposes that provider selection as a first-class control. Web search
results use a stable normalized shape:

```text
title
url
description
```

Web search providers are for outbound result discovery. They should generally filter out provider
self-links and same-provider navigation links.

### Pagination Schemas

`pagination_schemas` stores reusable pagination behavior learned from a representative page.
The goal is the same as extraction schemas: an agent does the page-specific reasoning once, Atlas
validates the plan, and future runs reuse the durable schema until it fails and needs repair.

Each row captures:

- `id`
- `identity_key`
- `match`
- `enabled`
- `priority`
- `next_button_selector`, nullable
- `item_selector`
- `expected_max_item_count`, nullable
- `query_param_key`
- `query_param_value_template`
- `start_value`
- `value_step`
- `domain`, nullable
- `path`, nullable
- `generated_from_crawl_id`, nullable
- `generated_from_artifact_id`, nullable
- `generated_by_task_run_id`, nullable
- `inputs_json`
- `validation_status`, nullable
- `failure_count`
- `last_failed_at`, nullable
- `last_error`, nullable
- `warnings_json`
- `created_at`
- `updated_at`

Pagination is query-param based for now. Click and scroll expansion are different, more browser-stateful
problems and should not be part of the active primitive until query pagination has proven itself.

The query template stores raw, unencoded values. URL construction is responsible for encoding:

```json
{"query_param_key":"page","query_param_value_template":"{{value}}","start_value":1,"value_step":1}
```

```json
{"query_param_key":"page_token","query_param_value_template":"v1:{{value}}","start_value":0,"value_step":1}
```

```json
{"query_param_key":"offset","query_param_value_template":"{{value}}","start_value":0,"value_step":30}
```

Validation must prove that `item_selector` finds repeated items and that advancing produces new
items rather than only a successful browser action. New schemas should be born as candidates in the
primitive, not durable rows. The durable row is only created after Atlas has:

1. crawled the first page,
2. used a pagination candidate agent to detect `next_button_selector`, `item_selector`,
   `query_param_key`, `query_param_value_template`, `start_value`, and `value_step`,
3. verified and counted page 1 items locally,
4. constructed and crawled page 2 from the query template,
5. verified and counted page 2 items locally, and
6. confirmed page 2 contains new items.

If candidate validation fails, Atlas should feed the concrete selector/count/advance error back into
the pagination agent up to the configured attempt limit. The `paginate` primitive reports per-page
item counts, new item counts, the strategy used, and the stop reason.

New schemas should start with a conservative query-stripped path prefix match, such as
`https://example.com/search*`. Admin tooling can later widen that to a broader glob like
`*example.com/item/*` once repeated pages prove they share the same pagination behavior.

## Relationships

```text
Url 1 -> many Crawls
Url 1 -> many Artifacts, optional on Artifact
Crawl 1 -> many Artifacts, optional on Artifact
TaskRun 1 -> many Crawls
TaskRun 1 -> many Artifacts, optional on Artifact
TaskRun many -> one ExtractSchema, optional
ExtractSchema many -> one generated-from Crawl, optional
ExtractSchema many -> one generated-from Artifact, optional
CrawlPolicy applies to URLs by runtime match pattern, not by foreign key
ExtractSchema applies to URLs by runtime match pattern, not by foreign key
PaginationSchema applies to URLs by runtime match pattern, not by foreign key
```

Cached reuse should be represented separately from production. A task run can use crawls or
artifacts produced by earlier task runs.

Usage join tables:

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

## Planned Schema Snapshot

This is the current target table shape.

```text
urls
- id
- url, unique
- normalized_url, unique
- scheme
- host
- domain
- path
- query_fingerprint, nullable
```

```text
tasks
- id
- name
- primitive
- input_json
- schedule_json, nullable
- identity_key, nullable unique
- created_by_effect_run_id, nullable
- updated_by_effect_run_id, nullable
- archived_by_effect_run_id, nullable
- archived_at, nullable
- archived_reason, nullable
- last_run_at, nullable
- next_run_at, nullable
- created_at
- updated_at
```

```text
task_runs
- id
- task_id
- status
- trigger_kind
- triggered_by_effect_run_id, nullable
- extract_schema_id, nullable
- queued_at
- leased_by, nullable
- leased_at, nullable
- leased_until, nullable
- started_at, nullable
- finished_at, nullable
- input_json
- output_json, nullable
- warnings_json
- error, nullable
- created_at
- updated_at
```

```text
task_effects
- id
- task_id
- effect_type
- config_json
- enabled
- position
- created_at
- updated_at
```

```text
effect_runs
- id
- effect_id
- source_run_id
- status
- operation
- target_task_id, nullable
- target_run_id, nullable
- input_json
- output_json, nullable
- error, nullable
- started_at
- finished_at, nullable
- created_at
- updated_at
```

```text
crawls
- id
- url_id
- task_run_id
- started_at
- finished_at, nullable
- duration_ms, nullable
- inputs_json
- input_hash
- success
- status_code, nullable
- redirects_json
- errors_json
- retry_count
- warnings_json
- meta
- created_at
```

```text
artifacts
- id
- crawl_id, nullable
- url_id, nullable
- task_run_id, nullable
- kind
- path or storage_uri
- content_type
- size_bytes
- sha256
- input_hash, nullable
- meta
- warnings_json
- invalidated_at, nullable
- invalidated_reason, nullable
- created_at
```

```text
task_run_crawls
- task_run_id
- crawl_id
- role
- created_at
```

```text
task_run_artifacts
- task_run_id
- artifact_id
- role
- created_at
```

```text
extract_schemas
- id
- identity_key, unique
- match
- enabled
- priority
- prompt
- prompt_hash
- schema_type
- target_json_hash, nullable
- domain, nullable
- path, nullable
- schema_json
- schema_hash
- generated_from_crawl_id, nullable
- generated_from_artifact_id, nullable
- generated_by_task_run_id, nullable
- inputs_json
- validation_status, nullable
- failure_count
- last_failed_at, nullable
- last_error, nullable
- warnings_json
- created_at
- updated_at
```

```text
crawl_policies
- id
- match
- enabled
- config
- created_at
- updated_at
```

`extract_schemas.match` and `crawl_policies.match` are runtime glob matches against URLs, not
foreign keys to `urls`.

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

## Policies

Operational policies such as concurrency, crawl rate limits, and cache reuse should live behind
the same task/crawl chokepoints as execution. API, CLI, scheduler, and worker paths should all
consult the same policy service before spending browser/network budget.

Like extract schemas, crawl policies should be URL-pattern scoped:

```text
CrawlPolicy.match = *example.com/item/*
CrawlPolicy.config = {
  "max_concurrency": 5,
  "backoff_strategy": "exponential"
}
```

That means all matching item detail pages on `example.com` get the same crawl behavior.

Each crawl policy row should capture:

- `match`
- `enabled`
- `config`
- `created_at`
- `updated_at`

Policy matching should use the same glob-style URL matching semantics as index filters. Keep the
table deliberately small until repeated policy behavior proves which fields deserve first-class
columns.

- global crawl concurrency
- per-domain crawl concurrency
- per-domain recent crawl volume limits
- per-URL or input-hash dedupe while a matching run is queued or running
- cache reuse rules
- warning tolerance rules

Policies can start from environment defaults and later grow API/database overrides:

```text
MAX_GLOBAL_CRAWL_CONCURRENCY
MAX_DOMAIN_CRAWL_CONCURRENCY
MAX_EXTRACT_ATTEMPTS
ARTIFACT_CACHE_AGE_SECONDS
```

Environment defaults act as fallback policies. Database `crawl_policies` are the operator-facing
control surface.

The policy service should answer questions such as:

- Can this task run start another crawl now?
- Should this URL/input hash use a fresh crawl or a cached artifact?
- Is there already an in-flight task run for this URL/input hash?
- Has this domain produced enough recent warnings that new crawls should slow down or fail fast?

Do not scatter concurrency checks through primitives. Primitives request crawl or artifact
material; the chokepoint decides whether that means fresh crawl, cache reuse, wait, skip, or fail.

## Task-Backed Ad Hoc Execution

User-facing API and CLI action triggers should use the same task-run path as scheduled work:

1. Resolve or create the task for the action input.
2. Create a task run.
3. Execute the task run inline in the current API or CLI process.
4. Let the task executor mark status, warnings, crawls, artifacts, and schema provenance.

Ad hoc API and CLI runs should not enqueue work for the worker. Their task runs are created as
manual inline runs and executed immediately. Because the created tasks are unscheduled, the
scheduler should not pick them up later unless a user explicitly adds a schedule.

Action routes and CLI commands should not directly call primitives in a way that bypasses task
and run persistence. This keeps playground and ad hoc usage durable: if an operator tries
something and likes it, the resulting task can later receive a schedule and become production
work without changing shape.

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
codes, content hash, task primitive, and extraction schema requirements.

Manual invalidation should support URL-oriented workflows, such as invalidating artifacts for
one URL, a set of URLs, a domain, or a path group after crawl configuration changes.

## Extract Schema Reuse

Extraction should reuse schemas by finding an enabled schema whose `match` pattern matches the
query-stripped URL and whose prompt, schema type, and target shape match the current extraction
intent.

```text
hash(prompt + schema_type + target_shape + match)
```

Pages covered by the same explicit match should share the same schema for the same extraction
intent. This keeps search pages, listing pages, and detail pages from regenerating schemas once an
operator has widened the schema match.

Admin workflow:

1. Atlas creates schemas conservatively with exact query-stripped URL matches.
2. Admin reviews schemas grouped by domain/path similarity.
3. Admin selects schemas to merge.
4. Admin chooses the replacement match, such as `*example.com/item/*`.
5. Atlas keeps or regenerates one schema for that match and invalidates or removes the narrower
   duplicates.

Later automation can recommend merges after several similar schemas are created, but should still
present an explicit match pattern for approval.

Task runs should record the `extract_schema_id` they used, but not a version id.

## Implementation Sequence

Suggested order:

1. Add URL models, schemas, and Alembic migration.
2. Expand artifacts to support `url_id`, `crawl_id`, `kind`, `size_bytes`, `sha256`,
   `content_type`, `input_hash`, `invalidated_at`, `invalidated_reason`, and `warnings_json`.
3. Add Crawl models, schemas, and migration.
4. Route API and CLI action triggers through task creation, task-run creation, and inline
   task-run execution.
5. Introduce the durable crawl service and route the `crawl` action through it.
6. Add task-run usage joins for crawls and artifacts.
7. Add CrawlPolicy and ExtractSchema models and migrations.
8. Route schema generation and extract execution through reusable, self-healing schema records.
9. Replace manifest-style artifact knowledge with Postgres-backed lookups.
10. Add analytics/query helpers for domain volume, in-flight URL patterns, warning trends,
   and recent reusable artifacts.

## Open Questions

No open modeling questions at this checkpoint.
