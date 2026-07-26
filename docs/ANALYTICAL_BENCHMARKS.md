# Analytical benchmarks

This document defines how Atlas proves the analytical vision in [Vision](VISION.md). The benchmark
is a product and correctness contract, not a collection of favorable demo queries.

Atlas should answer questions whose results emerge from observations across pages, domains, and
time. The corpus therefore contains known hidden structure, misleading controls, and explicit
expected answers. A benchmark succeeds only when its result is correct, bounded, and traceable to
captured evidence.

## Principles

1. **Ground truth remains outside the queried lake.** Scenario manifests and expected answers live
   in the removable benchmark package. Benchmark SQL cannot join the labels that define the answer.
2. **Evidence uses the production contracts.** Semantic pages begin as deterministic raw HTML and
   pass through the ordinary DOM encoder and catalogue schema. Direct SQL generation may reproduce
   the same rows for the large load corpus only after an equivalence test proves the encoding.
3. **Noise is part of the test.** A small semantic pack is evaluated alone for correctness and
   overlaid on the large synthetic corpus for physical planning and performance.
4. **Every result is auditable.** Expected outputs include the crawl, document, URL, capture time,
   and derivation version needed to inspect their source observations.
5. **Coverage qualifies absence.** A page or entity is not classified as disappeared merely because
   Atlas stopped crawling it. Scenarios distinguish observed removal, continued absence, redirect,
   acquisition failure, and missing coverage.
6. **Independence is not domain count.** Mirrors, syndication, citations, shared ownership, and
   near-duplicate text deliberately create many-domain observations with one underlying origin.
7. **SQL stays ordinary.** Benchmark queries use DuckDB-compatible SQL and public catalogue
   definitions. They contain no Atlas optimizer hints or hidden benchmark-only functions.
8. **The baseline uses raw evidence.** Acceptance queries read the canonical lake tables directly
   or through ordinary CTEs, macros, and virtual views. Required materialized views, cached semantic
   tables, and benchmark-only indexes are prohibited.

## Environment roles

| Lake | Benchmark role |
|---|---|
| `atlas_test` | Small semantic corpus, exact correctness, compiler plan tests, and fast iteration |
| `atlas_load` | The same semantic signals inside the multi-billion-element load corpus |
| `atlas` | Production evidence only; analytical ground truth is never seeded |

The semantic pack is independently idempotent. Its deterministic identities and manifest make a
rerun verify or skip committed work without rewriting the background corpus.

## Repository isolation

The pack lives entirely under:

```text
benchmarks/analytical_ground_truth/
```

It is benchmark infrastructure, not a product fixture. Production setup does not seed it, backend
modules do not import it, no migration or canonical schema object refers to it, and it is excluded
from production packaging. The only allowed references outside the directory are:

- one or more explicit `make analytical-ground-truth-*` development targets;
- a short link from benchmark documentation; and
- an optional architecture test asserting that production code does not import the package.

Removing the directory, its Make targets, and documentation link must remove the feature completely.
There is no compatibility or migration obligation for a retired benchmark pack.

The package contains only compact sources:

```text
benchmarks/analytical_ground_truth/
  README.md
  manifest.json
  cli.py
  generator/
  templates/
  scenarios/
  queries/
  expected/
  tests/
```

Generated HTML, Arrow, Parquet, and result artifacts are never committed. Scenario manifests,
templates, authored SQL, and small expected-answer files are committed. `cli.py` imports public
Atlas repository boundaries for loading and verification; Atlas never imports the benchmark.

## Evidence and derived observations

The canonical lake schema remains limited to crawl and retained-content evidence. Benchmark
scenarios do not add `claims`, `products`, `entities`, or `organizations` as base tables.

Each benchmark is first expressed as one raw-lake query or a small set of ordinary virtual views. A
derived observation should carry:

```text
observation identity
crawl_id
document_id
page_url
captured_at
extracted subject, predicate, and value
extractor or derivation version
matching evidence or confidence, when applicable
```

Entity resolution, claim clustering, and independence analysis may produce additional user-owned
relations. Their rows must retain the contributing observation identities rather than replacing
evidence with an unexplained canonical label.

## Hero benchmarks

The first three benchmarks define the minimum convincing Atlas demonstration.

### 1. Claim lineage: repetition is not consensus

**Question:** Which claims appear widespread but originate from very few independent sources, and
which claims represent genuine independent agreement?

The corpus contains:

- one claim independently reported by many unrelated sources;
- one claim copied through a deep citation and syndication tree from one primary source;
- paraphrases that defeat exact-text deduplication;
- mirrors on different domains owned by the same organization;
- pages that cite an intermediary rather than the primary source; and
- a high-volume control claim with deliberately weak primary evidence.

The expected result distinguishes occurrence count, distinct domains, inferred organizations,
independent origins, citation depth, first observation, and supporting crawl evidence. Counting
domains alone must produce the wrong ranking, proving why provenance and lineage matter.

This benchmark exercises text extraction, claim normalization, near-duplicate detection, links,
ownership evidence, recursive lineage analysis, temporal ordering, and cross-domain aggregation.

### 2. Product market: identity across sites and time

**Question:** Which offers refer to the same product, how do their price and availability differ,
and which products or offers disappeared?

The corpus contains:

- canonical products sold by multiple independent retailers;
- stable GTIN matches and site-specific SKU aliases;
- products without a global identifier that require multiple pieces of matching evidence;
- misleading near-matches that differ by size, revision, bundle, or region;
- price and availability changes over time;
- renamed products and retailer-specific titles;
- one manufacturer operating several brands; and
- discontinued products, temporary stockouts, redirects, and crawl-coverage gaps.

Expected answers include canonical product groups, accepted and rejected matches, price history,
first and last observations, current offers, and disappearance classification. Every normalized
offer retains its source page and crawl.

This benchmark exercises record extraction, identity resolution, temporal aggregation,
cross-domain grouping, exact and fuzzy evidence, and absence under measured coverage.

### 3. Emerging phenomenon: independent acceleration before naming

**Question:** What cluster of independent observations is accelerating before it has a canonical
name or consolidated source?

The corpus contains:

- an unnamed event observed independently by a known sequence of unrelated sources;
- vocabulary variants that share concrete entities, places, measurements, or effects;
- a later canonical name that must not leak into earlier snapshots;
- a high-volume copied story with the same mention curve but one origin;
- a recurring seasonal topic that is popular but not novel; and
- random low-volume noise.

Expected answers identify the planted event during the interval after independent acceleration but
before canonical naming. The copied and seasonal controls must rank below it. The result retains the
observations that explain its acceleration, independence, and novelty scores.

This benchmark exercises temporal windows, clustering inputs, source independence, novelty against
history, canonical-entity coverage, and explainable anomaly ranking.

## Supporting benchmarks

These narrower workloads isolate reusable capabilities needed by the hero benchmarks.

### Cross-domain linkage graph

Extract HTTP(S) anchors from a bounded crawl population and emit weighted
`source_domain -> target_domain` edges with distinct source-page counts and first/last observation
times. The corpus contains known communities, bridges, reciprocal links, site-wide boilerplate, and
duplicate anchors. A page count and an occurrence count must remain distinguishable.

### Organizational topology

Infer domain groups from legal names, addresses, contact details, analytics identifiers, authors,
social profiles, content reuse, and links. Shared hosting or a common CDN is a negative control and
must not merge otherwise unrelated organizations.

### Content propagation

Trace exact and near-duplicate content through first observation, citation links, paraphrases, and
later copies. The benchmark distinguishes the earliest Atlas observation from a claim of true
authorship.

### Digital disappearance

Identify content that was repeatedly observed and then returned a durable removal response while
the surrounding site remained covered. Controls include redirects, temporary failures, robots or
policy exclusion, and pages whose crawl schedule ended.

### Latent connection

Plant two independently named bodies of knowledge with similar relational structure and useful
shared mechanisms but almost no citations between them. The expected result identifies the
cross-cluster structural correspondence without treating a generic embedding similarity as proof.
This is an advanced benchmark: user-defined feature extraction or models may propose candidates,
while SQL measures supporting observations, separation, and missing links.

## Ground-truth pack

The first implemented slice is the product-market scenario. Exact identities and counts are frozen
in its manifest before implementation. Its target is deliberately small:

- 14 days with 10 deterministic observation points;
- 12 retailer domains under distinct registrable domains;
- 24 canonical products, each offered by four retailers;
- at most 1,000 crawl observations;
- realistic repeated URLs and document reuse when content does not change;
- a small number of generated HTML templates; and
- configurable irrelevant DOM filler that does not change the expected semantic answer.

Semantic evidence should occupy only a small fraction of each document. The existing load corpus
provides the large irrelevant backdrop; the pack should not add billions of rows merely to imitate
scale already present. The product slice passed end to end before claim lineage was added as the
second scenario. The emerging-phenomenon scenario remains a later milestone.

### Scenario sources

The removable benchmark package separates:

```text
benchmarks/analytical_ground_truth/
  manifest.json
  templates/
  scenarios/
    product_market.json
    claim_lineage.json
  queries/
    product_market.sql
    claim_lineage.sql
  expected/
    product_market.json
    claim_lineage.json
```

Templates and generator modules produce deterministic pages and content fragments without checking
in one file per page. Scenario files define identities, observation schedules, relationships, and
controlled variants. `expected/` contains canonical result rows or invariants used only by the test
runner. A later scenario adds its own manifest, query, and expected-answer files only when work on
that milestone begins.

The generated HTML must not expose scenario labels, canonical entity IDs, independence classes, or
expected rankings. Those are external truth used to judge the query, not hidden columns available
to it.

### Complexity budget

The benchmark remains smaller than the product it tests:

- one CLI owns `plan`, `load`, `verify`, and `run` commands;
- one scenario owns its generator inputs, authored queries, and expected answers;
- generated pages and lake exports are temporary artifacts, never repository files;
- the initial scenario uses existing Atlas and Python dependencies only;
- there is no benchmark API, worker, queue, database schema, plugin system, or UI;
- there is no generic entity-resolution or scenario framework before a second scenario proves a
  shared abstraction;
- each scenario starts with one hero query and a small number of correctness controls; and
- scenario scale comes from manifest parameters and the existing `atlas_load` backdrop, not copied
  fixture files.

Some duplication between the first two scenario generators is preferable to a speculative
framework. Shared code moves into `generator/` only when two active scenarios use the same contract.
The committed package should stay reviewable as source and small expected results, not become a
data repository.

### Determinism and idempotency

- Crawl IDs derive from `(pack version, scenario, source, scheduled observation)`.
- Document IDs remain ordinary content hashes.
- URLs, timestamps, graph provenance, attempts, and outcomes are deterministic.
- Reuse points to identical raw bytes rather than duplicating a truth label.
- Reruns preflight deterministic crawl identities and verify committed counts before skipping.
- A pack version is immutable. Changing evidence or expected answers creates a new version.
- Partial batches can be retried without duplicating crawls, documents, or elements.

### Coverage model

The pack includes an explicit schedule for which pages Atlas attempted to observe. Expected answers
derive coverage from ordinary crawl evidence, not from a public benchmark-only table. This allows
queries to distinguish:

| Observation | Interpretation |
|---|---|
| Repeated success followed by stable `404` or `410` under continued site coverage | Evidence of removal |
| Success followed by a stable redirect | Moved, not disappeared |
| Repeated acquisition failure | Current state unknown |
| No scheduled or admitted crawl | No coverage claim |
| Successful surrounding-site crawls but a consistently absent linked entity | Qualified negative evidence |

## Benchmark query contract

Each benchmark consists of:

1. authored DuckDB-compatible SQL and any ordinary user-owned definitions;
2. the expected compiler outcome and required document, domain, or time scope proof;
3. expected result rows or invariants;
4. evidence checks proving result provenance;
5. phase timings for compilation, scope resolution, hydration, execution, and result transfer; and
6. a negative variant that must be rejected or produce a deliberately different result.

Queries select evidence through crawl relationships, time, domain, or authored identifiers. They do
not hard-code document IDs obtained from the expected-answer manifest.

## Performance tiers

Correctness is mandatory at every tier. Latency targets describe different work and must not be
collapsed into one promise:

| Tier | Intended behavior |
|---|---|
| Bounded interactive extraction | A selective page or document population should normally answer within 10 seconds on the production-sized Quack instance |
| Bounded cross-domain analysis | A query with selective authored crawl, time, or domain scope should remain practical over the raw lake |
| Broad historical analysis | May take longer than 10 seconds, but planning and execution must remain proportional to the evidence the authored predicates select |
| Deliberate global DOM analysis | Requires an explicit analytical budget and has no general sub-10-second promise |

The benchmark records connection setup separately from compilation, remote planning, execution, and
result transfer. “Cold” means no intentional warm-up; it does not claim to evict DuckLake,
object-store, Quack, operating-system, or network caches.

## Compiler implications

The benchmarks drive generic compiler work rather than workload-specific shortcuts:

- relationship-derived document scoping through nested CTEs, views, and macros;
- partition and row-group pruning from explicit document and time predicates;
- physical late loading of expensive `attributes`, text, and reconstructed HTML;
- shared bounded scans when several extraction branches use the same evidence;
- stable provenance projection through normalization and aggregation;
- plan and cardinality diagnostics before expensive remote execution; and
- fail-closed behavior for unbounded managed DOM scans.

The compiler does not decide what constitutes a claim, product, organization, expert, or emerging
event. It proves that the user's chosen derivation can execute safely over the selected raw
evidence.

Required hidden materializations are outside the initial benchmark. If raw-lake performance later
demonstrates a hard physical limit, Atlas may evaluate transparent accelerators. They must preserve
ordinary SQL semantics, remain invisible to authored queries, and be justified by measured
workloads rather than assumed in the acceptance criteria.

## Acceptance

The first milestone is complete when:

1. the product-market scenario loads idempotently into `atlas_test`;
2. its authored SQL recovers the exact expected answers and rejected near-matches with evidence
   provenance;
3. baseline queries use only canonical lake tables plus ordinary virtual SQL definitions;
4. the same semantic pack can be overlaid on `atlas_load` without weakening correctness;
5. bounded extraction and cross-domain analytical queries meet their respective latency tier; and
6. an intentionally unbounded DOM variant is rejected before execution.

Passing only a small isolated corpus is not sufficient. Passing only a large scan with no known
answer is not sufficient. Atlas proves the vision when it finds the planted, auditable signal
inside the realistically large body of irrelevant web evidence. Claim lineage is implemented as the
second isolated correctness slice; both slices still need the `atlas_load` overlay before the
scale claim is complete. Emerging-phenomenon detection is the third milestone.

## Executable packs

Both implemented slices live entirely under
[`benchmarks/analytical_ground_truth/`](../benchmarks/analytical_ground_truth/README.md). Removing
that directory and its explicit Make targets removes the benchmark; production code does not import
it.

The product-market workflow is:

```sh
make analytical-ground-truth-test
make analytical-ground-truth-plan
make analytical-ground-truth-load
make analytical-ground-truth-verify
make analytical-ground-truth-run
```

The claim-lineage workflow is:

```sh
make analytical-ground-truth-claim-plan
make analytical-ground-truth-claim-load
make analytical-ground-truth-claim-verify
make analytical-ground-truth-claim-run
```

`load` passes generated HTML through `RepositoryIngestor`, so content hashing, immutable raw-object
storage, the normal HTML5 DOM encoder, staged Parquet projection, and canonical DuckLake commits are
all exercised. A complete rerun verifies related counts before skipping every deterministic crawl.
`run` compiles against the live catalogue metadata, executes through the production interactive
runtime so compiler document scope is hydrated normally, compares every result and evidence
identity with the external oracle, and proves the unbounded DOM control is rejected before
execution.

The claim-lineage query derives measurable facts from paraphrased article prose, reads declared
citation anchors from the same scoped DOM population, and uses a recursive CTE to find independent
roots. Its controls prove that 12 domains can represent one origin while eight domains can represent
eight origins. This is a proof of evidence extraction, explicit citation lineage, publisher-aware
root counting, and recursive cross-domain SQL. General semantic claim equivalence, hidden
syndication, undeclared ownership, and inferred citations remain outside this slice.
