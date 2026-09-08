# Public v1 implementation and acceptance

Status: installed and functionally verified locally on 2026-09-08. An intermittent
DuckDB process failure remains an explicitly recorded runtime limitation.

## Objective

Install the versioned public catalogue end to end, update the public site's
documentation and executable examples, and verify their use through the public UI.
Keep the existing evidence, registry, materialization and query-service ownership
boundaries. Physical optimizations are not part of this change.

## Agreed interface

One namespace, `public_v1`, replaces `web` and `content`. New query API requests
default to this version and support unqualified relation/helper names. Explicit
unsupported versions fail rather than silently selecting another contract. Query
results identify the resolved schema version separately from the lake snapshot.

- `capture`: `capture_id`, `requested_url`, `effective_url`, `captured_at`,
  `http_status_code`, `content_id`, `byte_length`, `encoding`.
- `html_node`: `content_id`, `node_index`, `parent_index`, `subtree_end_index`,
  `sibling_index`, `node_type`, `name`, `namespace`, `value`.
- `html_element`: `content_id`, `node_index`, `parent_index`, `subtree_end_index`,
  `sibling_index`, `tag`, `namespace`, `attributes`, `text_direct`.
- `link`: `capture_id`, `node_index`, `raw_href`, `resolved_url`.
- `subtree_text(content_id, node_index)`: ordered descendant text, excluding comments
  and text outside the selected node's subtree.

Captures have retained bytes, including retained HTTP error responses. No public
observation table exposes failed acquisitions without content. Internal visit
identities remain unchanged and supply capture identities. Content identity is the
hash of exact retained bytes. Repeated captures share content structures. Node and
element identifiers agree; indices are depth-first, subtree ends are exclusive,
and sibling positions count all node types. Node references are snapshot-scoped.
Direct element text concatenates immediate child text in order. Neither direct nor
subtree text claims browser visibility semantics. Raw-byte retrieval uses content
identity, never public storage paths. Coverage request membership is exposed as capture.request_ids,
a sorted unique UUID list. Operational collection, fulfillment and acquisition-reason
tables are not exposed in public SQL.

SCHEMA.md defines SQL types/nullability, node kinds, attribute namespaces and
projection-availability semantics.

## Work and evidence gates

- [x] Update architecture/schema/query/cutoff documentation for the direct replacement.
- [x] Implement faithful nodes and aligned elements/links in the existing projection lifecycle.
- [x] Install the complete registry-owned `public_v1` catalogue and remove old namespaces.
- [x] Default and validate query schema versions, support unqualified SQL, and report version/snapshot.
- [x] Update SDK and all query consumers, helper discovery, examples and assistant context.
- [x] Update public documentation, homepage examples and SQL workbench content.
- [x] Provide and verify raw-byte retrieval by content identity without widening query credentials.
- [x] Run targeted semantic tests and `make check`, including both frontend builds/typechecks.
- [x] Build/deploy local processes and install/rebuild/activate a consistent generation.
- [x] Run catalogue validation against the installed lake.
- [x] Through public UI, run an unqualified capture query and an element extraction example.
- [x] Through public UI, verify node/subtree text and capture-context links on known evidence.
- [x] Verify public documentation links, editable examples, and CSV export.
- [x] Verify SDK parity, explicit version rejection, and reported version/snapshot.
- [x] Exercise a low-depth, low-concurrency crawl through ingestion and materialization.
- [x] Audit every objective requirement against current evidence before completing the goal.

## Initial inspection

The initial worktree was clean and contained no `public_v1` references. The public
registry still defines `web.observation`, content elements, objects, links and
lineage. Local Compose services are running; public is exposed on port 8080, query
on 8010 and API on 8000. These facts do not establish installation of the new
contract. Do not reset source evidence to avoid implementing the rebuild path.

## Implementation progress (2026-09-08)

- Implemented registry-owned public_v1 relations/helper, complete parsed node tree,
  shared element/link positions, and content-root readiness marker.
- Query service selects public_v1, permits documented unqualified names, rejects
  other versions, and returns schema_version. SDK requests and saved dataset
  definitions preserve the version.
- Updated public reference, docs, dataset examples, assistant context, shell
  discovery, and raw-content download route.
- `make check` completed successfully after the contract updates, including Python,
  SDK, shared packages, and public/admin typechecks, tests and production builds.
- Raw-content tests verify exact bytes, attachment headers, hash lookup and absence
  of orphaned evidence. Public access permits only hash-scoped GET downloads.
- Core/public Docker images built. Setup completed without resetting source evidence.
- Replacement rebuild 09fea8c5-c8ca-4e54-ba1b-7db6db9f8d24 completed:
  source snapshot 2079, activation snapshot 2088, 587 visits, two batches,
  504,675 output rows. All services were restored and report healthy.
- Setup was recreated from the current core image. Post-activation
  `make catalogue-check` passed.

Local verification logs: /tmp/periplus-public-v1-check.log,
/tmp/periplus-v1-catalogue.log, /tmp/periplus-v1-public-final-check.log,
/tmp/periplus-v1-public-final-tests.log, and /tmp/periplus-v1-public-final-build.log.

### Rebuild recovery

The first rebuild failed before activation after finding a pre-existing duplicate
content lifecycle fence. Snapshot 2015 already had two unretired rows for content
6140bf7a69055ec0f10f467f745fa6bca43bdd6e6f1efe02cce9baacc34bda30
(revisions 0 and 1). With all evidence writers/materializers stopped, a transaction
replaced those two rows with the single unretired revision-1 row. No source evidence
or raw bytes changed; a complete duplicate scan returned no remaining duplicates.
The root cause of the pre-existing duplicate is not established.

Replacement rebuild: 09fea8c5-c8ca-4e54-ba1b-7db6db9f8d24, snapshot 2079,
using the standard 500-visit batch size. Materializers were resumed with
`docker compose up -d --no-deps periplus-materializer`. Avoid `compose start` here:
it attempted to rerun the old stopped setup container through dependencies.
The setup service was subsequently recreated from the current core image and succeeded.

## Runtime acceptance evidence

- Public browser at localhost:8080: unqualified capture query returned five rows;
  CSV download contained its header and five rows. Homepage extraction link opened
  editable public_v1 SQL and returned 20 book title/price/source/date rows.
- A node/element join and lateral subtree_text returned the same h1 at node 112
  for content 9fe4eaf03c16535a08b508569fec826fbcb11ecd904abd24ce9419e8ba7ff5b7.
  Capture-context links for example.com returned the IANA destination.
- Fixed the browser-discovered old schema grouping and SQL completion dictionary.
  Both now derive or select public_v1. Public typecheck, 40 tests, and production
  image build passed after the fixes; the deployed explorer exposes all eight views.
- Nine standalone docs/dataset SQL examples returned rows without truncation
  through the public proxy. The collection example used the fresh collection below.
- Source SDK qualified/unqualified queries returned identical rows, reported
  public_v1 and snapshot 2118, and rejected public_v2 with HTTP 422. Helper discovery
  and DESCRIBE of all five core views succeeded. A raw download of 18,843 bytes
  matched the content ID using SHA-256.
- Fresh collection 70d6ac35-432e-4cfd-ab59-f6bd2c58ff63 requested example.com at
  depth zero, page limit one, and zero reuse age. It supplied one page, reused none,
  ingested one, and reported active_generation_committed/query_ready=true.
  Public capture 3e96aa66-78ee-4779-bbb7-a3030615f71a has captured_at
  2026-09-08T04:28:18.668260Z, joins to its fulfillment and six text nodes,
  and resolves the page's IANA anchor.

### Runtime limitation

The first browser node/subtree query aborted the DuckDB process at 04:28:44 UTC
with an out-of-range vector fatal error. Compose restarted it; the exact SQL then
returned the correct row, and subsequent documentation queries succeeded. This
matches the pre-existing connection-dependent failure category in UPSTREAM.md.
Functional acceptance does not establish that this intermittent engine fault is
fixed or that production reliability has been demonstrated. No schema workaround,
automatic SQL replay, or speculative engine fix was introduced.

## Public membership simplification

The public contract exposes request membership through capture.request_ids, keeping
one row per retained capture. The separate membership view is removed. Coverage SQL
links, documentation and schema discovery filter the UUID list directly. Internal
fulfillment evidence and operational APIs are unchanged. Catalogue setup replaces
the view and removes superseded public objects; no materialization rebuild is needed.

## Import retirement

Removed the external HTML upload route, import service and metadata models,
Common Crawl test-corpus script/launcher, import-only configuration and tests.
All visit evidence now follows native policy/timing/attempt validation. Public
capture has nine columns; source provenance fields and the private ingest.visits
provenance struct are removed. Public schema remains public_v1; the private
physical contract is 11.0.0.

The local lake contained 1,392 native visits and no external visits. With the
crawler stopped and ingestion pending/ack-pending/dead letters all zero, workers
were stopped and public views plus the private column removal were applied in
one transaction. No capture or raw object was deleted. Native collection selection
provenance remains unrelated and intact.

Import-retirement verification: make check passed (564 Python tests, 33 skipped,
plus SDK/shared/public/admin checks and builds). The installed capture view has
nine columns and ingest.visits has no provenance column. The upload route is absent
from OpenAPI. Native collection 02b8dbe0-c980-49ed-8ccd-f0dc035a0589 supplied and
ingested one newly acquired page; its 559-byte raw download matched its content hash.

The existing live consumer stalled at the source-schema boundary (UPSTREAM.md).
A standard rebuild was started. Run 7a584a1c-01b9-44c0-a180-0f32e245c369 completed
two of three 500-visit batches, but its final batch contained 498 MB of raw input
and repeatedly stalled/restarted. With materializers stopped, it was marked failed
and replaced by a26f29c2-19b3-4259-a6fe-294628837006 using 25-visit batches.
The existing active generation and source evidence were preserved.

Import retirement completed locally: rebuild a26f29c2-19b3-4259-a6fe-294628837006
activated at snapshot 5266 after all 56 batches (1,393 visits, 8,241,521 output rows).
Post-activation collection 42463b34-e0ac-477b-8af9-32375de9c873 acquired one new page,
reused none, ingested it, and reached active_generation_committed/query_ready=true.
Public SQL joined its capture to the Example Domain h1. Final catalogue validation
passed, and the retired POST /ingest/html endpoint returns 404.

## Public object removal

Removed the public object relation and exposed logical `byte_length BIGINT` directly
on capture. The public contract now has five views and the subtree_text helper.
The existing content download endpoint remains available using capture.content_id.
Private document references, content-addressed raw storage, partitions and sort
orders are unchanged; no materialization rebuild is required.

Local verification: `make check` passed (564 Python tests, 33 skipped, plus SDK,
shared packages and both frontend checks/builds). Setup and catalogue validation
passed. The public gateway returns capture.byte_length as BIGINT and rejects
public_v1.object with 422. A downloaded 16,808-byte capture matched both its
reported byte_length and SHA-256 content_id. All local services are healthy.

## HTML-only public captures

The public capture view now includes only detected text/html (case-insensitive),
matching the active HTML pipeline, and omits media_type and representation.
Both remain in private evidence. HTML response bodies and rendered HTML retain
the same content identity semantics; captures do not wait for DOM readiness.
Public hash downloads apply the same HTML filter; private document access is
unchanged. This edit is local; no catalogue deployment is performed by the side
conversation.
