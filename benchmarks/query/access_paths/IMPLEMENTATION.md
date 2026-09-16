# Runtime implementation of the access-path findings

The runtime adopts four physical relations: canonical documents, flat elements,
captures and focused JSON-LD scripts. Full subtree text is stored on each element;
there is no document-array copy in ClickHouse. This chooses the measured working
preview path and accepts its extra encoding, storage and indexing cost.

Native indexes cover normalized URLs, document words, element class tokens,
attribute keys/values, direct/subtree words and JSON-LD types/names. The expensive
Map-dependent id projection and broad JSON-word index are absent. An additional local 100,000-capture optimizer probe did not select a document-offset
projection for raw download lookup. A native exact index on the nullable document
ID was selected for the same query, so captures use that index and the URL index;
the old content projection is removed. This generated fixture tests plan selection,
not corpus capacity or compression. Public document IDs use the
same hexadecimal String representation as the physical sorting key.

No custom query rewrite is added. The reader's native
`optimize_functions_to_subcolumns=0` setting preserves the indexed class expression.
New public `document` and `json_ld` views expose the smaller useful access surfaces.
Existing element, capture, link and page columns retain their meanings.

## Publication and retries

The existing exact claims and uncertain-write drain remain authoritative. The
writer sends at most 131,072 rows or the existing 96 MiB target per RowBinary block
(with the existing 128 MiB hard request bound). A single larger permitted row can
exceed the target, never the hard bound. Character spans are sliced before UTF-8
encoding. Rows repeat the complete document projection digest, avoiding a separate
random hash's storage cost per element. Before/after lookups are bounded by the
block's document/node ranges and reject duplicates/conflicts.

Elements and JSON-LD scripts precede the document completion row; captures follow.
Retries fill missing identities. Public membership filters exclude unfinished
writes and documents without complete captures. Removing a last retained reference
also cleans unfinished child rows, even if the capture itself was never inserted.
This is not a native transaction across tables; see the
[documented insert guarantees](https://clickhouse.com/docs/concepts/features/operations/insert/transactions). Build activation remains the
existing Postgres publication-pointer operation, and query bindings still pin a
single build. Cross-table snapshot isolation during concurrent live writes or
retirement is not provided by this change.

## Limits that this implementation does not solve

- Public whole-corpus element `COUNT(*)` still traverses visibility membership.
  The experimental report explicitly left that problem unresolved. Do not remove
  the guard or claim metadata-only counts. `sum(element_count)` on public documents
  is a useful explicit alternative, not an acceptance-case substitution.
- Global token search still consults parts; storage growth and merge capacity remain
  material. No unmeasured hash partitioning or NVMe-to-NAS lifecycle is deployed.
- Full subtree text can expand substantially within deep documents. Existing source
  and projection limits remain, and failures stay visible rather than truncating.
- Attribute-value indexes have a real measured storage cost. The report's bundle
  estimate excluded them; it must not be quoted as this runtime's exact footprint.
- JSON-LD handles cover top-level names/types only; raw scripts retain other structure.
- Local functional/recovery proof is not a new retained-corpus throughput benchmark
  or a billion-document capacity certification.

## Validation and rollout

Run `make check` and the isolated native-layout tests:

```sh
cd packages/periplus
PERIPLUS_TEST_ACCESS_PATHS=1 uv run python -m unittest discover -s tests -p test_material_access_paths.py
```

The native tests create fresh randomly named databases and a temporary reader on
local Compose ClickHouse, then remove them. They cover original public SQL through
QueryService, native index selection, Unicode/subtree values, a lost successful
write response, different retry block boundaries, conflicts, same-body reuse with
different base URLs, and last-reference/unpublished-row retirement.

`scripts/archive_recovery_smoke.py` exercises fresh Postgres/NATS/ClickHouse and real
workers on an isolated network, including actual worker death and ownership expiry,
archive-only reconstruction, pause/live catch-up, failed input repair, activation,
cancellation and old-target reclamation. Generated artifacts stay under `.artifacts`. `--smoke-only` repeats fresh-store
recovery without fault injection; it does not replace the full lifecycle drill.

Deployment requires a new recipe-aware worker image, hidden archive rebuild,
verification, and ordinary activation. Old builds need their matching worker image
until retired. Do not run the new DDL against existing tables or deploy by merely
refreshing public views. Homelab deployment is not part of this implementation.

## Local results (16 September 2026)

- Nine layout tests passed against local ClickHouse 26.8.2.7, including native
  reader execution and selected index plans. The document lookup probe uses
  100,000 generated captures solely to test plan selection.
- Ten historical benchmark-runner tests passed.
- The final index layout reconstructed 255 generated retained captures and 1,275
  unique elements from a copied archive into fresh Postgres/NATS/ClickHouse, with
  empty business tables and a respected tombstone. The worker recovery phase took
  about 25 seconds; this small fixture is a correctness smoke test, not throughput
  evidence for real pages.
- The fault-injection drill used the same row writer/publication protocol before
  the final document-projection-to-index substitution and raised bounded row ceiling.
  The final writer separately verified 100,000 element rows in one bounded insert. Actual worker death recovered
  after 650 seconds with the full 610-second ownership fence. Missing/corrupt-input repair, activation, cancellation and old-target reclamation
  also passed. The separate lifecycle result is recorded in `.artifacts/native-access-paths/recovery/`.

Full-table element counts and cross-table transactional snapshots remain outside
these passing claims. Deploy only through a fresh build; coordinate the API's
physical document-ID lookup change with the serving-build cutover.
