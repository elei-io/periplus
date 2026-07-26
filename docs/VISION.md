# Vision

Atlas turns changing web pages into durable, queryable evidence.

Search answers a question when somebody has already written the answer down. Atlas is for questions
whose answers emerge only after observations from many pages, sites, and times are connected:

> What can be inferred from the totality of captured web evidence that no single page explicitly
> records?

That is the product thesis. Crawling and scraping have historically been separate jobs: one system
collects pages and another extracts records from a page-shaped input. Atlas keeps acquisition
provenance, immutable content, structural DOM evidence, time, and analytical SQL in one system so a
query can move across pages and domains without losing where any fact came from.

## Evidence before answers

A crawl should remain useful beyond the request that caused it. Atlas retains immutable raw HTML and
a versioned structural projection so later extraction and analysis can reuse the same evidence.
Crawl policies, graph topology, edge SQL, catalogue definitions, and analytical schemas are
explicit, inspectable knowledge rather than hidden behavior inside a browser call or a
site-specific traversal loop.

Atlas does not make an aggregate trustworthy merely by producing it. A derived observation should
retain enough provenance to reach its evidence:

- crawl and document identity;
- page URL and capture time;
- source domain and graph provenance;
- extractor or derivation version; and
- any confidence or matching evidence introduced by the derivation.

Claims, products, people, organizations, places, and concepts are not universal base-table facts.
Users define those analytical meanings with ordinary DuckDB-compatible SQL. CTEs, macros, and
virtual views organize that SQL without changing the evidence contract. User-owned materialization
is optional and must not be required to make natural queries over the canonical lake viable. The
canonical lake preserves evidence; derived relations express hypotheses over it.

## The analytical loop

Atlas is designed around one continuous path:

```text
URL inputs
  -> crawl graphs
  -> immutable captures and structural evidence
  -> compiler-scoped SQL over the raw lake
  -> observations and relationships
  -> cross-page, cross-domain, and temporal analysis
  -> evidence-backed crawl inputs or published tables
```

The compiler is central to this loop. Users still write ordinary SQL. Atlas uses managed relation
identity, lineage, stable keys, physical layout, and execution budgets to scope the work without
requiring optimizer hints, precomputed system views, or a second query language. Atlas should make
queries over `crawls`, `documents`, and `elements` as selective as their authored relationships
allow. Transparent physical acceleration may be considered later, but it cannot become part of the
logical user contract.

## Questions Atlas should make possible

Atlas should provide the evidence and analytical machinery for questions such as:

- Which conclusions recur among genuinely independent expert sources?
- Which widely repeated claims descend from one original source?
- What phenomenon is appearing across independent sites and accelerating before it has a canonical
  name?
- What information, product, project, or community disappeared while the surrounding web remained
  observable?
- Which domains appear to belong to the same organization when contact details, legal entities,
  identifiers, authorship, links, and content reuse are considered together?
- Which products are the same across sellers, names, countries, and time?
- Which bodies of knowledge contain structurally similar solutions but almost no citations or links
  between them?

Atlas does not prescribe one definition of expertise, independence, identity, evidence strength,
or novelty. It makes those definitions expressible, repeatable, and auditable.

## What Atlas does

- Acquires pages through one crawl path and retains immutable evidence.
- Composes bounded searches, pagination, and site walks as crawl graphs.
- Exposes crawl history and a structural DOM projection through DuckDB-compatible SQL.
- Derives typed observations and relationships from retained pages.
- Supports cross-page, cross-domain, temporal, and graph-shaped analysis.
- Lets users organize and optionally persist their own derived relations.
- Publishes stable tables that downstream analytical tools can query through snapshots or changes.
- Preserves the provenance needed to inspect why an aggregate answer exists.

## What Atlas is not

- A claim that Atlas has crawled the whole web.
- An oracle that decides truth, expertise, identity, or causality for the user.
- A web-scale distributed crawler.
- A general workflow orchestration platform.
- A browser farm or transport-provider framework.
- A replacement for DuckDB's optimizer or a new SQL dialect.
- A requirement that every global historical query complete interactively.
- A destination connector platform or general data-pipeline orchestrator.
- A collection of per-action workers, queues, and storage systems.
- A compatibility museum for storage models the project has left behind.

Absence is meaningful only relative to measured crawl coverage. Repetition is not independence.
Similarity is not identity. A generated cluster is a hypothesis until its evidence supports the
interpretation. Atlas must retain these distinctions rather than turn analytical convenience into
false certainty.

## Product principles

1. Preserve evidence; derive interpretations from it.
2. Make every important aggregate answer traceable to captured observations.
3. Give each kind of state one authoritative owner.
4. Keep authored analysis ordinary DuckDB-compatible SQL.
5. Make expensive or stateful behavior bounded and observable.
6. Optimize natural queries over canonical evidence before introducing physical accelerators.
7. Prefer a direct implementation over a framework without an active caller.
8. Keep capacity permits separate from durable work and correctness leases.
9. Delete completed migration scaffolding and unused surfaces.

The system remains operable by a small team. Ambition in the questions Atlas can support does not
justify hidden ownership, unbounded queues, or speculative services. New machinery must unlock a
demonstrated analytical capability and preserve the direct evidence path.

## Proof

Atlas proves this vision with deterministic analytical benchmarks, not screenshots or one-off
demos. The benchmark corpus plants known cross-domain, temporal, lineage, and identity signals
inside a much larger body of irrelevant DOM evidence. Atlas must recover the expected answers,
retain their provenance, and reject unsafe work before execution. The baseline proof runs over the
canonical raw lake schema without required materializations.

[Analytical benchmarks](ANALYTICAL_BENCHMARKS.md) defines that ground-truth contract.
