# Crawl Data Model Target

This document describes the target durable data model for Atlas crawl knowledge. It is the
reference point for comparing current implementation progress against the product direction.

Atlas has three user-facing execution surfaces: API, CLI, and scheduled tasks. All of them should
share the same task-run and crawl behavior. API and CLI routes should stay thin; durable behavior
belongs in actions, artifacts, tasks, and the domain services around them.

## Product Goal

Atlas should build reusable knowledge from crawled pages:

- which URLs and page shapes have been seen,
- which browser visits happened and what they produced,
- which artifacts can be reused,
- which query parameters a page shape appears to support,
- which extraction plans work for a page shape,
- which tasks and effects created or mutated durable knowledge.

Postgres stores facts, identities, provenance, timings, statuses, and compact learned plans.
Artifact storage stores large bytes such as HTML, screenshots, PDFs, and MHTML.

The model should be inspectable and operator-editable. A human should be able to see why Atlas is
reusing a schema, widen or narrow its URL match, invalidate artifacts, and understand which task
run produced a piece of durable knowledge.

## Target Groups

The target model has four groups.

Observed web identity and storage facts:

- `domains`
- `paths`
- `query_params`
- `urls`
- `crawls`
- `artifacts`

Runtime execution facts:

- `tasks`
- `task_runs`
- `effects`
- `effect_runs`

Learned reusable page-shape plans:

- `query_schemas`
- `extract_schemas`
- possibly `pagination_schemas` later, only if pagination needs a specialized derived table

Applicability controls:

- `url_matches`
- match patterns on policies and schemas while a shared match table is not yet justified
- `crawl_policies`

## Current Distance

Implemented now:

- `urls`
- `crawls`
- `artifacts`
- `tasks`
- `task_runs`
- `task_effects`
- `effect_runs`
- `task_run_crawls`
- `task_run_artifacts`
- `extract_schemas`
- `pagination_schemas`
- `crawl_policies`

Partial:

- `urls` currently stores `domain`, `path`, and `query_fingerprint` as columns instead of using
  separate `domains`, `paths`, and `query_params` tables.
- Schemas and policies currently use their own `match` strings instead of a shared `url_matches`
  table.
- `task_effects` currently represents effects. The target language can still use `effects`, but
  a rename is not required until the name becomes confusing in API or UI surfaces.
- `pagination_schemas` exists, but it was premature. Treat it as a legacy/specialized query-param
  plan until the general `query_schemas` primitive exists.

Missing:

- `query_schemas`
- first-class schema mutation provenance such as `updated_by_task_run_id`
- normalized `domains`, `paths`, `query_params`, and `url_matches`

The next milestone is `query_schemas`.

## Core Principles

### Crawl Is The Acquisition Chokepoint

`crawl` is the only primitive that should spend browser/network budget to acquire page bytes.
Task-backed actions that need page contents should call through the shared crawl path with their
task-run context.

The crawl path is responsible for:

1. resolving or creating URL identity records,
2. checking cache eligibility,
3. executing Crawl4AI when a fresh remote visit is needed,
4. recording crawl facts,
5. writing artifacts,
6. linking task runs to produced or reused crawls and artifacts.

Reprocessing stored HTML with Crawl4AI `raw:` is not a new crawl. It is artifact reuse by the
current task run.

### URL Shape Is Durable Knowledge

URL identity is not just a string. Atlas should eventually know the domain, path, and query
parameters as queryable dimensions. The first implementation can keep those as columns on `urls`;
normalization should happen when analytics, UI grouping, or matching workflows need it.

### Schemas Apply To Page Shapes

Schemas should not directly belong to one exact URL. They apply to URL patterns that represent
page shapes. The target matching control surface is explicit and inspectable: a pattern can be
shown in the UI, edited, widened, narrowed, merged, or disabled.

Until a shared `url_matches` table exists, schema and policy rows can carry their own `match`
string.

### Learned Plans Need Provenance

Generated and mutated schemas should record where they came from:

- the crawl that supplied the representative page,
- the artifact whose bytes were analyzed,
- the task run that generated the schema,
- the task run that most recently updated or repaired it.

Detailed historical versioning is not a first requirement. A compact mutation pointer is enough
until a real audit workflow needs schema event history.

## Entities

### Domains

Target-only for now.

`domains` represents registrable or operational domains used for grouping, rate limits, analytics,
and policy decisions.

Potential fields:

- `id`
- `domain`
- `created_at`

Do not add this table just to reduce duplication. Add it when domain-level workflows need stable
identity.

### Paths

Target-only for now.

`paths` represents reusable URL path dimensions within a domain. It is useful for analytics,
page-shape grouping, and admin workflows that merge schemas across similar paths.

Potential fields:

- `id`
- `domain_id`
- `path`
- `created_at`

### Query Params

Target-only for now.

`query_params` represents observed query keys and values on URLs. This is different from
`query_schemas`: `query_params` stores observed URL facts, while `query_schemas` stores learned
plans about what parameters a page shape appears to support.

Potential fields:

- `id`
- `url_id`
- `key`
- `value`
- `position`
- `created_at`

### URLs

`urls` is the set of unique URLs known to Atlas.

Current fields are close to the near-term target:

- `id`
- `url`
- `normalized_url`
- `scheme`
- `host`
- `domain`
- `path`
- `query_fingerprint`

`urls` is a stable dimension used by crawls, artifacts, task matching, cache lookup, and analytics.

### Crawls

`crawls` represents one point in time where Atlas actually visited a remote URL.

Target fields:

- `id`
- `url_id`
- `task_run_id`
- `crawl_policy_id`, nullable target
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
- `created_at`

Crawls are transport records. Keep first-class columns minimal and put evolving Crawl4AI/browser
details in JSONB until repeated queries justify promotion.

### Artifacts

`artifacts` represents bytes stored on disk or object storage.

Target fields:

- `id`
- `crawl_id`, nullable
- `url_id`, nullable
- `task_run_id`, nullable
- `kind`
- `path` or `storage_uri`
- `content_type`
- `size_bytes`
- `sha256`
- `input_hash`
- `meta`
- `warnings_json`
- `invalidated_at`
- `invalidated_reason`
- `created_at`

Artifacts can originate from a crawl and can be reused by later task runs. Cache invalidation
belongs on artifacts, not on crawls.

### Tasks

`tasks` is persisted schedulable work.

Target fields:

- `id`
- `name`
- `primitive`
- `input_json`
- `schedule_json`
- `identity_key`
- `created_by_effect_run_id`
- `updated_by_effect_run_id`
- `archived_by_effect_run_id`
- `archived_at`
- `archived_reason`
- `last_run_at`
- `next_run_at`
- `created_at`
- `updated_at`

### Task Runs

`task_runs` is one execution attempt of a task.

Target fields:

- `id`
- `task_id`
- `status`
- `trigger_kind`
- `triggered_by_effect_run_id`
- `extract_schema_id`, nullable
- `query_schema_id`, nullable target
- `queued_at`
- `leased_by`
- `leased_at`
- `leased_until`
- `started_at`
- `finished_at`
- `input_json`
- `output_json`
- `warnings_json`
- `error`
- `created_at`
- `updated_at`

Task runs should record which durable schemas they used when that relationship affects output.

### Effects And Effect Runs

Effects describe follow-on behavior attached to tasks. Effect runs record executions of those
effects.

The current table name is `task_effects`. That is acceptable unless product language settles on
standalone reusable effects.

Important relationships:

- `Task -> many Effects`
- `Effect -> many EffectRuns`
- `TaskRun -> many EffectRuns`
- `EffectRun -> target Task`, nullable
- `EffectRun -> target TaskRun`, nullable

### Query Schemas

`query_schemas` is the next target milestone.

A query schema is a reusable plan for the query-parameter surface available on a page shape. It is
learned by looking at crawled page contents and page-adjacent evidence, including:

- the current URL,
- links,
- forms,
- filter controls,
- sort controls,
- pagination controls,
- embedded app state,
- canonical or alternate URLs,
- network hints when available.

It answers:

- which query parameters appear to exist,
- what values are observed or inferable,
- what each parameter seems to do,
- how confident Atlas is,
- how to construct candidate URLs for future actions.

Target fields:

- `id`
- `identity_key`
- `match`
- `enabled`
- `priority`
- `domain`, nullable
- `path`, nullable
- `params_json`
- `schema_hash`
- `generated_from_crawl_id`, nullable
- `generated_from_artifact_id`, nullable
- `generated_by_task_run_id`, nullable
- `updated_by_task_run_id`, nullable
- `inputs_json`
- `validation_status`, nullable
- `confidence`, nullable
- `failure_count`
- `last_failed_at`, nullable
- `last_error`, nullable
- `warnings_json`
- `created_at`
- `updated_at`

Example `params_json`:

```json
{
  "params": [
    {
      "key": "keyword",
      "kind": "text",
      "required": false,
      "observed_values": ["16tb ironwolf"],
      "purpose": "search term",
      "source": "current_url",
      "confidence": 0.95
    },
    {
      "key": "sort",
      "kind": "enum",
      "required": false,
      "observed_values": ["newest", "price_asc", "price_desc"],
      "purpose": "sort order",
      "source": "filter_links",
      "confidence": 0.8
    },
    {
      "key": "page",
      "kind": "pagination",
      "required": false,
      "value_template": "{{value}}",
      "start_value": 1,
      "value_step": 1,
      "source": "next_link",
      "confidence": 0.9
    }
  ]
}
```

The first implementation should keep `params_json` flexible JSONB but define Pydantic contracts
at API and action boundaries.

Validation should prove at least one of:

1. the parameter and value were observed in page links or forms,
2. constructing a URL with the parameter yields a successful crawl with meaningfully related
   content,
3. the parameter is inherited from the current URL and preserved by page navigation.

Query schemas are discovered affordances with confidence and provenance, not guaranteed complete
truth. A single page may expose only part of a site's query surface.

### Extract Schemas

`extract_schemas` stores reusable Crawl4AI extraction schemas for a page shape and extraction
intent.

Target fields:

- `id`
- `identity_key`
- `match`
- `enabled`
- `priority`
- `prompt`
- `prompt_hash`
- `schema_type`
- `target_json_hash`
- `domain`, nullable
- `path`, nullable
- `schema_json`
- `schema_hash`
- `generated_from_crawl_id`, nullable
- `generated_from_artifact_id`, nullable
- `generated_by_task_run_id`, nullable
- `updated_by_task_run_id`, nullable target
- `inputs_json`
- `validation_status`, nullable
- `failure_count`
- `last_failed_at`, nullable
- `last_error`, nullable
- `warnings_json`
- `created_at`
- `updated_at`

Extraction schema reuse should match on URL pattern plus extraction intent. If a schema fails,
Atlas can repair it in place and update mutation provenance.

### Pagination Schemas

`pagination_schemas` exists today, but it should not lead the model.

Pagination is one kind of query-parameter behavior. After `query_schemas` exists, pagination
should either:

- live as `kind: "pagination"` entries inside `query_schemas.params_json`, or
- remain a small specialized table derived from and linked to `query_schemas` if dedicated
  pagination validation needs justify it.

Until then, avoid expanding `pagination_schemas` further.

### URL Matches

Target-only for now.

`url_matches` would represent reusable, operator-editable URL pattern scopes.

Potential fields:

- `id`
- `match`
- `enabled`
- `description`
- `domain`
- `path`
- `created_by_task_run_id`
- `updated_by_task_run_id`
- `created_at`
- `updated_at`

Do not add this table just to make relationships look tidy. Add it when multiple schemas and
policies need to share, merge, and audit the same match scopes.

### Crawl Policies

`crawl_policies` stores URL-pattern-scoped crawl behavior.

Current target fields:

- `id`
- `match`
- `enabled`
- `config`
- `created_at`
- `updated_at`

Policies should eventually answer:

- can this task run start another crawl now,
- should this URL/input hash use fresh crawl or cached artifact,
- is there already in-flight work for this URL/input hash,
- has this domain produced enough recent warnings to slow down or fail fast.

Do not scatter concurrency, cache, and warning-tolerance checks across primitives.

## Relationship Target

```text
Domain 1 -> many Paths target
Domain 1 -> many URLs target
Path 1 -> many URLs target
URL 1 -> many QueryParams target
URL 1 -> many Crawls
URL 1 -> many Artifacts
Crawl many -> one URL
Crawl many -> one TaskRun
Crawl many -> one CrawlPolicy target
Crawl 1 -> many Artifacts
Artifact many -> one URL, nullable
Artifact many -> one Crawl, nullable
Artifact many -> one TaskRun, nullable
Task 1 -> many TaskRuns
Task 1 -> many Effects
TaskRun 1 -> many Crawls
TaskRun 1 -> many Artifacts
TaskRun many -> one QuerySchema, nullable target
TaskRun many -> one ExtractSchema, nullable
Effect 1 -> many EffectRuns
EffectRun many -> one source TaskRun
EffectRun may create or mutate Tasks
QuerySchema applies to URLs by match pattern
ExtractSchema applies to URLs by match pattern
CrawlPolicy applies to URLs by match pattern
```

Reuse should be represented separately from production:

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

Initial cache eligibility:

1. Match by `url_id` and crawl input hash.
2. Require an HTML artifact exists and is present on disk.
3. Require `invalidated_at is null`.
4. Require no artifact warnings, or only explicitly allowed warnings.

Artifact invalidation is the cache-death mechanism. Cleanup should only delete artifacts that are
already invalidated.

## Implementation Sequence

Foundation already mostly exists:

1. URL records.
2. Crawl records.
3. Artifact records and invalidation fields.
4. Task and task-run execution records.
5. Task-run usage joins for crawls and artifacts.
6. Extract schemas.
7. Crawl policies.

Next:

1. Add `query_schemas` model, Pydantic schemas, migration, service, and API/admin surfaces.
2. Add a query-schema generation action or shared service that crawls a representative page,
   inspects page contents, extracts candidate query parameters and values, validates them, and
   records provenance.
3. Record `query_schema_id` on task runs when a run uses a query schema.
4. Rework `paginate` so it consumes query-schema pagination entries or deliberately remains a
   derived specialized path.
5. Add `updated_by_task_run_id` to learned schemas when repair/mutation behavior becomes active.
6. Add normalized `domains`, `paths`, `query_params`, and `url_matches` only when workflows need
   them.

## Open Questions

- Should pagination live only inside `query_schemas.params_json`, or remain a derived specialized
  table?
- What validation threshold is required before Atlas automatically reuses a discovered query
  parameter?
- Should schema mutation provenance stay as `updated_by_task_run_id`, or become a lightweight
  schema event table?
- When do URL dimensions need to become normalized tables instead of columns and derived views?
