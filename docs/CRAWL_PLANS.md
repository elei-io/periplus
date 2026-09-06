# Crawl plans

Periplus begins every native crawl with one or more URLs. Every start URL enters
the frozen plan's root node, and run-wide URL deduplication applies across all
roots and derived links. The caller chooses one of two traversal strategies:

```python
crawl = periplus.crawl(
    [
        "https://en.wikipedia.org/wiki/Sauli_Niinist%C3%B6",
        "https://en.wikipedia.org/wiki/Tarja_Halonen",
    ],
    depth=2,
    relation_scope="same_origin",
)
```

or:

```python
crawl = periplus.crawl(
    [
        "https://en.wikipedia.org/wiki/Sauli_Niinist%C3%B6",
        "https://en.wikipedia.org/wiki/Tarja_Halonen",
    ],
    plan="my-custom-plan",
)
```

`plan` is mutually exclusive with `depth` and `relation_scope`.

## Built-in finite-depth plan

Periplus represents `depth=N` as a frozen linear plan with `N + 1` acquisition
nodes and `N` edges. Every start URL enters depth zero. An edge from depth `i`
to depth `i + 1` selects links from the page acquired at depth `i`. This makes
the depth limit durable and exact even across retries and worker restarts.

`relation_scope` is an inclusive navigation boundary:

- `same_origin` includes `self` and `same_origin`.
- `same_host` additionally includes `same_host`.
- `same_site` additionally includes `same_site`.
- `external` permits every scope, including `external`.

If neither a stored plan nor a depth is supplied, Periplus acquires only the
submitted start URLs.

## Stored plans

Users create stored plans in the UI. A plan contains acquisition nodes, one
root node, and directed navigation edges. Every run freezes the saved plan
before admitting its start URLs.

Manual runs and schedules use the same start-URL list contract. Each scheduled
occurrence admits the schedule's complete list as one graph run.

An edge is one DuckDB `SELECT` query over the current page's ephemeral
`nav.links` relation. It must return a column named `url`.

```sql
SELECT target_url AS url
FROM nav.links
WHERE relation_scope IN ('self', 'same_origin')
```

The navigation relation includes:

```text
content_sha256
source_url, source_scheme, source_host, source_port
source_registrable_domain, source_path, source_query
target_url, target_scheme, target_host, target_port
target_path, target_query, target_fragment
relation_scope
raw_href, element_index
crawl_id
```

Plan SQL cannot read files, call table functions, attach databases, or access
`ingest.*`, `material.*`, `web.*`, `content.*`, or user data. It runs in a bounded
in-memory DuckDB connection with external access and extension loading
disabled.

Historical joins are deliberately not part of acquisition plans. They couple
frontier progress to catalogue availability, snapshot semantics, and query
compilation while adding no requirement to the current acquisition path.
Historical evidence remains queryable after acquisition and can inform a
later, explicit crawl submission if a real workflow requires it.

## Admission and completion

The [frontier destination](FRONTIER.md) preserves the plan and run identities below while
separating durable admission from acquisition dispatch. It is a pending runtime replacement,
not a second traversal strategy or an implemented change to this contract.

The normalized URL is the only admission identity within a crawl run. If
several edges select the same URL, Periplus acquires it once. This rule has no
user-facing mode.

The SDK lifecycle is:

```python
await crawl.completed()
await crawl.materialized()
```

`completed()` waits for terminal acquisition-plan execution.
`materialized()` additionally waits until all visit and document evidence for
the crawl has crossed ingestion and the fixed materialization workloads. The
SDK may return immediately from `materialized()` when that barrier was
already reached.
