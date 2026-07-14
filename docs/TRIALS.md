# Crawl policy trials

This document defines Atlas's initial crawl-policy experimentation contract. Trials are deliberately
small: Atlas samples a bounded share of ordinary crawl requests, runs one shadow acquisition with a
different frozen policy, and records both outcomes in the existing DuckLake evidence model.

Trials produce evidence only. They never change an active CrawlPolicy, choose a policy for the
request being sampled, or gate normal graph execution.

## Product objective

Atlas needs to learn whether a different acquisition policy would have produced more useful page
evidence at an acceptable additional cost. It does not need a privileged reference crawler or an
external source of truth. Both sides of the comparison are ordinary Atlas acquisitions:

```text
trial T

use crawl
  the crawl the graph already requested
  frozen policy: HTTP

sample crawl
  one shadow acquisition of the same requested URL
  frozen policy: browser / static
```

The use crawl is the free control observation. Only the sample crawl adds remote work.

An external provider may later be one candidate policy, but its result is not inherently more
correct than an Atlas HTTP or browser result.

## Vocabulary

Every durable crawl has a `purpose`:

- `use` is an acquisition requested for normal Atlas operation;
- `sample` is an experimental acquisition whose only product is evidence.

`use` and `sample` are intentionally plain product terms. A sample is still acquired, retained,
parsed, and ingested through the ordinary Atlas repository contract; its purpose only changes its
runtime side effects.

## Minimal durable contract

One DuckLake `crawls` row is one acquisition. Trial provenance is sparse typed metadata on that
ordinary crawl row:

```text
purpose   VARCHAR NOT NULL  -- use | sample
trial_id  UUID NULL
trial_sampler_version INTEGER NULL
trial_sample_rate DOUBLE NULL
trial_candidate_strategy VARCHAR NULL
trial_candidate_template VARCHAR NULL
trial_template_registry_version INTEGER NULL
```

The valid states are:

| Purpose | Trial ID | Meaning |
| --- | --- | --- |
| `use` | null | Ordinary crawl that was not selected for a trial |
| `use` | set | Incumbent result for the trial |
| `sample` | set | Shadow result produced by the challenger policy |

`purpose = 'sample'` with a null `trial_id` is invalid. V1 permits one `use` crawl and one `sample`
crawl per trial. If Atlas later runs multiple challengers, it may add an explicit arm identity then;
V1 does not add that abstraction before it has a caller.

No separate trial, acquisition, or crawl-configuration table is required. The shared `trial_id` is
the join key and `purpose` identifies the incumbent and challenger. All trial metadata is null for
ordinary unsampled crawls, which keeps the common row compact and compressible.

## Policy provenance already exists

Each crawl records:

- `crawl_policy_id` and `crawl_policy_revision`, when the acquisition used an editable policy;
- `profile` and the resolved ranked `template`;
- the complete frozen acquisition behavior in `config_json`; and
- `config_hash`, the SHA-256 fingerprint of that canonical configuration.

The trial preserves those fields exactly as for any other crawl. It never relies only on a mutable
policy identifier. The sample may use a system-generated challenger rather than an editable
matching policy; `config_json` remains authoritative even when no active CrawlPolicy owns it.

Unlike the superseded input hash, `config_hash` deliberately excludes the URL. It answers whether
two acquisitions used the same frozen behavior and makes policy cohorts cheap to group. URL
identity remains in the typed URL columns.

The nullable `trial_*` columns record the sampling decision, probability, candidate-selection
algorithm, and template-registry version. Together they make the dataset reproducible and preserve
the probability with which an example entered it.

## Request sampling

V1 samples a configured share of all eligible use requests. Selection is independent of observed
quality so that apparently successful pages are represented alongside obvious failures.

The initial settings are conceptually:

```text
ATLAS_POLICY_TRIAL_SAMPLE_SHARE
ATLAS_POLICY_TRIAL_MAX_IN_FLIGHT
ATLAS_POLICY_TRIAL_SAMPLER_VERSION
ATLAS_POLICY_TRIAL_PROVIDER_ENABLED
```

The share must be between zero and one. The in-flight ceiling is a hard operational bound; the
percentage alone is not safe when request volume or challenger cost changes.

Sampling defaults to `0` and is therefore opt-in. A first deployment can set the share to `0.01`
without changing the durable contract. Provider trials remain disabled independently until their
credentials, budget, and operational limit have been configured.

Sampling is deterministic rather than process-random:

```text
hash(crawl_request_id + sampler_version) < sample_share
```

Changing worker replica counts must not change which requests are selected. The `trial_id` and the
sample request identity are derived deterministically from the use request and sampler version, so
redelivery or concurrent admission cannot create duplicate trials.

This V1 is request-weighted: high-volume origins contribute more examples. Domain- or path-stratified
sampling may be introduced later as a separately identified sampling population, after traffic data
shows that it is needed.

## Candidate selection

V1 creates exactly one challenger. It does not sweep all policy presets. Atlas owns one canonical,
versioned acquisition-template registry ordered by increasing effort:

| Rank | Template | Behavior |
| ---: | --- | --- |
| 0 | `http_fast` | Plain HTTP without a browser |
| 1 | `static_fast` | Browser through DOM ready without an additional wait |
| 2 | `static_stable` | Static browser acquisition with DOM/link stability detection |
| 3 | `dynamic_scan` | Full-page scan and scrolling |
| 4 | `dynamic_stable` | Full-page scan plus stability detection |
| 5 | `app_stable` | Longer hydration allowance for client-rendered applications |
| 6 | `app_deep` | Application mode with deeper scrolling and a longer delay |
| 7 | `provider` | Configured external provider; disabled for trials unless explicitly enabled |

The normal challenger is `rank(current) + 1`. Rank describes acquisition effort and cost, not a
guarantee that a result is better. The frozen trial metadata records `candidate_template` and
`template_registry_version` so evidence remains interpretable after the registry changes.

The challenger policy is frozen before publication and does not create or mutate an editable URL
match. Sample acquisitions use cache mode `refresh` so an earlier cached result cannot masquerade
as new challenger evidence. Both acquisitions may execute concurrently; neither waits for the
other.

Testing only more expensive policies is acceptable for the initial evidence-gathering release, but
it cannot be the permanent strategy. Atlas must eventually sample cheaper challengers for origins
already using browser or provider policies, otherwise policy recommendations can only ratchet cost
upward. The long-term objective is the cheapest policy that provides adequate evidence.

## Execution contract

Trials reuse the existing transport-specific acquisition and ingestion implementations. A sample's
frozen profile routes it to the normal HTTP, browser, or provider worker fleet. V1 does not add a
combined trial worker or a parallel repository pipeline.

A sample is not an ordinary second graph admission. Graph admission deduplicates URLs and accounts
every admitted request against graph progress. A sample request instead uses a deterministic shadow
identity and the same acquisition delivery machinery without becoming graph work.

The distinction is enforced by `purpose`:

| Side effect | `use` | `sample` |
| --- | ---: | ---: |
| Acquire and retain raw HTML | yes | yes |
| Ingest a crawl, document, and elements | yes | yes |
| Count toward GraphRun progress | yes | no |
| Gate GraphRun completion | yes | no |
| Publish navigation readiness | yes | no |
| Evaluate outgoing graph edges | yes | no |
| Trigger user materialization | yes | no |
| Be eligible for another policy trial | yes | no |

The sample still carries the use crawl's originating graph, run, node, and URL provenance for
analytical comparison, plus its own deterministic shadow request identity. That provenance must not
be interpreted as graph-execution ownership.

Because V1 shares the existing transport queues and deployments, trials consume a deliberately
bounded fraction of normal worker capacity. This is a conscious simplicity tradeoff, not complete
resource isolation. Trial work still obeys the same Resource Governor remote-policy and
object-write permits as ordinary acquisition, in addition to its independent in-flight trial
budget. Increasing transport replicas cannot bypass either ceiling. If measured queue impact
becomes material, `sample` work may later move to a fixed lower-priority subject or dedicated
consumer without changing the DuckLake evidence contract.

## Storage behavior

Atlas already minimizes duplicate trial storage:

- raw HTML is immutable and content-addressed;
- documents are identified by HTML content;
- DOM elements belong to a document rather than a crawl; and
- crawl rows are lightweight acquisition observations.

If the use and sample policies return identical HTML, Atlas reuses the raw object, document, and DOM
projection and stores only the additional crawl observation. If they return different HTML, the new
document and elements are the evidence the trial exists to retain.

The sample share and in-flight ceiling bound growth, but storage bytes, new-document rate, and sample
DOM rows must be measured independently. A one-percent request sample is not guaranteed to add only
one percent storage because an elevated policy may return a substantially larger document.

## Querying trials

DuckLake contains enough evidence to start comparing trials without a feature store or trained
model. The basic pairing is:

```sql
SELECT
  use_crawl.trial_id,
  use_crawl.crawl_id AS use_crawl_id,
  sample_crawl.crawl_id AS sample_crawl_id,
  use_crawl.template AS use_template,
  sample_crawl.template AS sample_template,
  use_crawl.config_hash AS use_config_hash,
  sample_crawl.config_hash AS sample_config_hash,
  use_crawl.outcome AS use_outcome,
  sample_crawl.outcome AS sample_outcome,
  use_document.html_size_bytes AS use_html_bytes,
  sample_document.html_size_bytes AS sample_html_bytes,
  use_document.element_count AS use_element_count,
  sample_document.element_count AS sample_element_count,
  use_crawl.duration_ms AS use_duration_ms,
  sample_crawl.duration_ms AS sample_duration_ms
FROM crawls AS use_crawl
JOIN crawls AS sample_crawl
  ON sample_crawl.trial_id = use_crawl.trial_id
 AND sample_crawl.purpose = 'sample'
LEFT JOIN documents AS use_document
  ON use_document.document_id = use_crawl.document_id
LEFT JOIN documents AS sample_document
  ON sample_document.document_id = sample_crawl.document_id
WHERE use_crawl.purpose = 'use'
  AND use_crawl.trial_id IS NOT NULL;
```

The canonical `documents` row stores versioned primitive evidence computed while its DOM is already
being projected: HTML and visible-text size, element count, script/link/form/control counts, marker
counts, and a compact JSON vector of stable quality-flag codes. This evidence is computed once per
content-addressed document, not duplicated for every crawl that produced the same HTML. The
`elements` table remains available for less common structural analysis.

## Interpreting results

More HTML or more elements is not automatically better. A challenger may add framework wrappers,
menus, advertisements, or consent UI without adding useful evidence. Initial analysis should keep
separate dimensions such as:

- acquisition success and status;
- document-quality flags and acquisition-failure classes;
- visible-text volume;
- same-origin and total link counts;
- structural element counts and distributions;
- final URL agreement;
- acquisition duration; and
- transport or provider cost.

The initial query-time feature set deliberately stays inexpensive and explainable. Alongside the
paired deltas, Atlas calculates the mean, sample standard deviation, and coefficient of variation
of `visible_text_chars`, plus the ratio of distinct documents to distinct sampled URLs, separately
for the use and sample arms. These require no new acquisition data or feature store. Small cohorts
remain insufficient evidence; a single pair must not look conclusive merely because its variance
is zero.

The report assigns one conservative deterministic verdict: `awaiting_sample`,
`insufficient_evidence`, `promising`, `no_clear_gain`, `regressed`, or `inconclusive`. Every verdict
includes a reason derived from the displayed features. This is a presentation and review aid, not
an automatic policy mutation or a durable ML label. Atlas should eventually learn pairwise policy
preferences in context, not a literal mapping from one HTML tag to one configuration.

### Quality and acquisition-failure provenance

Quality flags are document-level interpretations of the versioned primitive measurements. The
current flags cover likely app shells, lazy-loading signals, and likely interaction requirements.
An empty flag vector means none of those versioned heuristics fired; it is not a general quality
guarantee. Keeping the primitive counts beside the flags lets future models learn from evidence
without treating today’s labels as timeless truth.

Acquisition failures are typed crawl outcomes. `outcome` is `success`, `partial`, or `failed`;
non-success rows record one stable `failure_code`, owning `failure_stage`, `failure_retryable`, and
a bounded diagnostic `failure_detail`. These fields cover HTTP/provider failures, browser
navigation errors, timeouts, oversized responses, exceptions, and missing HTML. Later ingestion,
graph-edge, and materialization failures remain in their owning operational state.

Trial comparison rows show the incumbent and challenger quality-flag medians as `use -> sample`.
They show acquisition failures as the number of completed paired crawls affected on each side;
using a median for sparse failures would hide them.

## Product surface

Policy trials live at `/crawl-policies/trials` as their own sidebar destination under **Policies**,
separate from the editable Crawl Policies page. Trials are evidence about policies, not another
kind of crawl run.

The initial page answers three questions in order:

1. Is sampling active, and what share is configured?
2. Is the observed all-time selection and pairing rate consistent with that setting?
3. For each domain and candidate, what is the current verdict and what evidence supports it?

The compact table has one row per origin and policy-comparison cohort, not one merged row per
domain. For example, `http_fast -> static_fast` and `static_fast -> static_stable` remain separate
rows with separately aggregated pair counts and verdicts. Expanding a row reveals the query-time
features: visible-text variation, distinct-document ratio, paired visible-text/HTML/element deltas,
identical documents, quality-flag progression, acquisition failures, recoveries, losses, and added
duration. This keeps every metric and Apply action attributable to exactly one challenger while the
collapsed table remains decision-focused. Historical evidence remains visible while sampling is
disabled.

An explicit **Apply** action may promote the one sampled template into a real CrawlPolicy. Applying
upserts the broad observed origin match (`scheme://host/*`) in Postgres and uses the canonical
template configuration without the trial-only cache refresh. The applied state is derived from the
effective CrawlPolicy rather than stored as a separate trial flag. More-specific path matches keep
their ordinary precedence, and the mutation requires a completed pair for that origin and template.

## No hot-path learning mutations

Sampling and shadow acquisition are the only trial actions in the request path. Trial evidence does
not update matching rules, activate policies, retry the use request, or alter the active graph.

Offline queries can immediately describe where challengers improve evidence. A later learning or
recommendation process may aggregate those results, but any policy activation remains a separate,
versioned control-plane action with its own review, canary, and rollback contract.

## V1 non-goals

The first implementation does not require:

- a paid reference provider;
- a separate trial worker fleet;
- a feature store;
- a quality-label table;
- an LLM in the trial loop;
- automatic policy activation;
- exhaustive policy sweeps;
- domain- or path-family sampling; or
- compatibility paths for crawl rows written before the greenfield schema change.

Because Atlas is greenfield, the DuckLake schema changes directly and disposable development state
is reset before testing the new contract.
