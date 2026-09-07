# Periplus Python SDK

Submit finite collection intent to Periplus's shared crawler:

```python
import periplus_sdk
from periplus_sdk import CollectionSpec

periplus_sdk.configure(api_url="http://localhost:8000", api_token="your-service-token")
collection = await periplus_sdk.collections.submit(CollectionSpec(
    seed_urls=("https://example.com/", "https://example.org/"),
    max_depth=0,
    page_limit=2,
))
await collection.wait(timeout=180)
collection.raise_for_status()
print(collection.snapshot.supplied_pages, collection.snapshot.query_ready)
```

`CollectionSpec` also accepts description-based discovery, seed SQL and parameters, page-local
follow SQL, section restrictions, a deadline, visibility, and a recent-result age bound. The server
normalizes URLs and enforces selection, access, and budget rules. A page limit means “up to N”.
Collections may share an acquisition while retaining their own selection and accounting.

Snapshots explicitly distinguish current execution from immutable history. `collections.list()`
uses bounded server pagination over retained current state; `collections.history()` returns lake
summaries and an opaque next cursor. Historical pagination is a live feed: refresh for late arrivals
above an already-consumed cursor. `collections.get(id)` resolves current or historical detail.
Partial historical outcomes keep unknown counters. Settlement does not prove catalogue readiness.

Lifecycle waits are local and cancellable, with a timeout covering polling and in-flight reads.
Temporary 429/503 responses honor bounded Retry-After delays. Timing out or cancelling a wait never
cancels the remote collection. Administrative callers can explicitly `pause()`, `resume()`,
`cancel()`, or `set_priority()` on current collections. Mutations are never retried automatically.
Pass an explicit UUID to `submit(..., id=identity)` when you need repeatable submission identity;
the server checks retained history before admitting that identity, so history must be available.

Inspect current work and durable results without starting new work:

```python
page = await collection.items(limit=20)
if page.next_after is not None:
    next_page = await collection.items(limit=20, after=page.next_after)

arrivals = await collection.arrivals(limit=20)
if arrivals.next_cursor is not None:
    older_arrivals = await collection.arrivals(limit=20, cursor=arrivals.next_cursor)

activity = await periplus_sdk.frontier.live()
if activity.history is None:
    print(activity.history_unavailable_reason)
if page.items:
    acquisition = await periplus_sdk.frontier.item(page.items[0].acquisition.id)
    print(acquisition.callers, acquisition.waiting_reason)
```

`collections.items(id, ...)` and `collections.arrivals(id, ...)` also work without a handle.
Current item ordering is by identity, not dispatch position; retired current state returns an API
not-found error. Arrivals remain readable from immutable history and distinguish definition,
fulfillment, observation, and unverified query-readiness milestones. Cursors are opaque and bounded;
refresh from the newest page for late commits. The SDK never walks an unbounded result set implicitly.

Live rates use committed evidence over explicit time windows and may lag acquisition. Missing
history stays `None`; it is not a zero rate. Current activity and caller previews obey the service
credential's visibility, while Live always describes public work. Reads preserve API errors and do
not initiate dispatch, mutate collections, or retry a failed call automatically.

Operators control the same crawler through versioned settings:

```python
state = await periplus_sdk.frontier.controls()
await periplus_sdk.frontier.replace_controls(
    state.settings.model_copy(update={"paused": True}),
    expected_version=state.policy_version,
)
```

Stale updates raise `ConflictError`; reload before deciding on a new change. Global pause lets
started captures finish. `frontier.domains()`, `create_domain()`, `update_domain()`, and
`delete_domain()` use typed domain policies and expected versions. These operations require an
administrative service credential. Requested pace is an upper bound, not promised throughput.

`periplus_sdk.conn.duck()` returns an ordinary read-only `duckdb.DuckDBPyConnection` attached to the
configured lake and validates the version-2 public relation set. The SDK is independent of the
backend Python package. SQL results use explicit lineage instead of an owning crawl column:

```sql
SELECT o.*
FROM web.observation o
WHERE EXISTS (
    SELECT 1 FROM web.fulfillment f
    WHERE f.observation_id = o.observation_id AND f.collection_id = CAST(? AS UUID)
);
```

Bind the collection UUID as the parameter. This preserves one row per observation even when
several collections share its acquisition. SQL selection and public catalogue semantics remain
server/lake contracts; the SDK does not compile or rewrite user SQL.

Install the released package from PyPI:

```sh
python -m pip install periplus-python-sdk
```

For local development, install directly from the repository:

```sh
python -m pip install ./packages/periplus-python-sdk
```

The standalone end-to-end proof is `examples/smoke.py`. Run it from an installed wheel after
initializing the replacement deployment. It performs a fresh one-page collection and separately
waits for public fulfillment, observation, and applicable HTML materialization. It uses
`PERIPLUS_API_URL` and the `PERIPLUS_DUCKLAKE_*`
connection variables, or `PERIPLUS_DIRECT_ENV_FILE`.

`periplus_sdk.conn.DuckLakeConnectionFactory` selects filesystem and S3 protocols centrally.
`periplus_sdk.conn.duck()` accepts an injected `DuckLakeConnectionProtocol` for another
DuckDB-supported data URI. S3-compatible endpoints use the
`PERIPLUS_DUCKLAKE_S3_ENDPOINT`, `PERIPLUS_DUCKLAKE_S3_REGION`,
`PERIPLUS_DUCKLAKE_S3_KEY_ID`, `PERIPLUS_DUCKLAKE_S3_SECRET_ACCESS_KEY`,
`PERIPLUS_DUCKLAKE_S3_SESSION_TOKEN`, `PERIPLUS_DUCKLAKE_S3_URL_STYLE`, and
`PERIPLUS_DUCKLAKE_S3_USE_SSL` settings; standard AWS credential variables are accepted when the
Periplus-specific credential variables are absent.

## Releasing

Repository CI publishes immutable releases from tags named
`periplus-python-sdk-v<version>`. The tag must exactly match the static version
in `pyproject.toml`; for example, version `0.1.0` is released with:

```sh
git tag periplus-python-sdk-v0.1.0
git push origin periplus-python-sdk-v0.1.0
```

PyPI publishing uses Trusted Publishing rather than a stored API token. The
PyPI publisher must be configured for GitHub owner `ekkuleivonen`, repository
`periplus`, workflow `python-sdk-release.yml`, and environment `pypi`. Protect
that GitHub environment with required reviewers before the first release.

Read durable provenance for an observation, including after current frontier rows are retired:

```python
from periplus_sdk import frontier

page = await frontier.lineage(observation_id, limit=20)
for record in page.items:
    print(record.kind, record.collection_id, record.reason, record.mode)
if page.next_cursor:
    next_page = await frontier.lineage(observation_id, cursor=page.next_cursor)
```

Capture reasons and result fulfillments are separate: a later reuse does not become an original
capture cause. Only committed visible evidence is returned; refresh the first page for late ingestion.
A missing observation and an unavailable catalogue remain distinct HTTP errors.


Current collection snapshots include `admission`: remaining frozen candidate entries, a preview
of at most five URLs, oldest known selection time and elapsed seconds at the snapshot time.
These entries may overlap or be excluded; they are not promised new pages or a queue position.
`estimate_unavailable_reason` distinguishes known admission constraints from missing comparable
wait observations. Discovery that has not frozen candidates has no invented wait duration.


`admission.estimate`, when present, has scope `first_admission` and predicts submission-to-first-interest
waiting for a direct single-URL request. It includes earliest/latest times, calculation and expiry
times, sample size and uncertainty. It does not predict completion or later batch admission. Missing,
stale or unsupported observations leave it null with `estimate_unavailable_reason`.
