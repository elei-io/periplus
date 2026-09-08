# Research-preview launch readiness audit

Date: 2026-09-08. Scope: current working tree based on `a391c9c9b51c1808706594a7fefcf18482bdf52b`, including substantial pre-existing uncommitted changes. No application fixes, deployments, live destructive probes, or configuration changes were made by this audit.

## Decision

**Do not open unrestricted anonymous crawling yet.** Fix poisoned-content materialization, establish browser egress isolation, and bound document/parse resource use first. Public SQL is substantially better isolated, but its admission bypasses and deployment resource containment need attention before unrestricted traffic. A restricted research preview is reasonable after those gates; a large architectural rewrite is not necessary.

Deployment assumption supplied by the owner: the control API and admin are LAN-only; only the public website is exposed through Cloudflare Tunnel. This materially reduces direct exposure. It does not prevent the website's public routes from invoking internal services, or a browser rendering attacker-controlled content from reaching LAN addresses if its network allows them. The actual tunnel, firewall, browser VLAN, storage permissions, backups, and deployed revision were not inspected.

Severity below reflects research-preview impact. P1 means fix or establish the named deployment control before exposing the affected feature. P2 means a concrete correctness/operational concern. An evidence gap is not a claim that the infrastructure lacks the control.

## 1. Unfinished work, leftovers, bugs, and complexity

### F1 — P1, reproduced: a tiny JSON-LD document can block materialization

`materialization/projections/jsonld_values.py:54` calls `json.loads` and catches only `TypeError` and `ValueError`. Nested JSON raises `RecursionError`; `_type_terms` also recursively walks arbitrary nesting without a depth bound.

Local reproduction: a 2,258-byte HTML document containing a JSON-LD script with 1,100 nested arrays raised `RecursionError` through the actual projector. No browser or production ingestion was used.

The consequence is larger than a failed optional extraction. `materialization/runtime.py:497-518` retries non-corruption live-batch exceptions with a one-second NAK. `materialization/live.py:332` waits for every applied marker before advancing the single CDC window. By code inspection, an ingested document of this shape can therefore hold subsequent live materialization behind it. A rebuild also encounters the same retained evidence and can fail permanently. Existing evidence remains stored; this is loss of catalogue progress, not demonstrated deletion.

Fix the format boundary: define a bounded parse policy, handle malformed/overdeep JSON locally, and represent any incomplete projection honestly. Do not ACK an unapplied batch or silently claim full readiness. Test malformed JSON, nesting, extreme attributes/text, and continuation past the offending observation through the real batch/CDC path.

### F2 — P1, code-confirmed: generation identity omits shared semantic dependencies

`materialization/registry.py:239-285` hashes each projection module's source and declaration. It does not hash the shared DOM encoder, link resolver, URL normalization, or relevant parser dependency versions. Those modules determine the rows produced. `PARSER_VERSION` and `PARSER_OPTIONS_HASH` exist in `materialization/dom/encoder.py`, but are not incorporated into the registry digest.

A change confined to the shared parser or link semantics can therefore produce different rows under the same accepted generation identity. Old content remains deduplicated against its existing marker while new content uses the new semantics. A deployment can appear generation-compatible without actually preserving projection semantics.

Include an explicit semantic version/dependency fingerprint covering shared transformation code and parser configuration. Keep the current generation model; add tests that a shared semantic change invalidates compatibility. Do not hash unrelated UI or documentation changes into the rebuild contract.

### F3 — P2, reproduced: malformed Retry-After is not safely contained

`crawl/acquisition/responses.py:34-44` accepts a parsed date without checking timezone awareness, then subtracts an aware UTC datetime. A timezone-less date reproduced `TypeError`. A 400-digit numeric header parsed as infinity; passing that to the adaptive pacing transition at `crawl/runtime/domain_pacing.py:387` reproduced `OverflowError`.

The acquisition service catches failures while recording adaptive pacing, so the latter is not evidence of a process-wide crash. The date error can escape response parsing; oversized delays can disable the intended pacing update. Treat invalid dates/nonfinite values as invalid input and impose a documented maximum accepted delay. Add fixtures for missing timezone, huge finite values, infinity, and valid HTTP dates.

### F4 — P2, code-confirmed: publication is not gated on the test workflow

`.github/workflows/publish.yml` builds and pushes core/frontend images after its metadata job; it does not depend on the separate CI workflow succeeding. CI and publication both trigger on main, while version-tag image publication does not itself run the full check suite. Docker builds establish buildability, not passing backend/security tests.

Make publish depend on checks for the exact revision, or enforce an equivalent verified promotion gate. GitHub branch protection and any external deployment gate were not inspected; they may reduce this risk, but the workflow alone does not enforce it.

### Leftovers worth removing or correcting

- **JSON-LD materialization has no discovered production reader.** Searches across backend/public source found its declaration and recursive helper, but no runtime consumer of `material.jsonld_values` or `type_terms`. It adds parse cost, files, generation coupling, and F1's failure surface. Confirm whether an external reader is an active caller. If none exists, remove the projection and rebuild the generation; if there is one, document it and harden the projection. This is a stronger simplification opportunity than cosmetic deduplication.
- `_web_old_dont_touch/` is tracked but excluded from npm workspaces and Docker build context. It is repository clutter, not a demonstrated deployed attack surface. Honor its name until the owner authorizes deletion; Git history can preserve an obsolete application when it is retired.
- `docs/QUERY.md` still describes `/datasets` and `/suggest`, which do not exist in the current route tree. Old audit documents refer to `/observatory`, now replaced by `/coverage`. `docs/SCHEMA.md` and `docs/RETENTION.md` retain private-request language despite the current shared-evidence contract. Historical audit results should remain dated records; current operating docs should describe only current contracts.
- The projection API has import-order coupling: importing a projection directly before the registry completed discovery failed locally because the registry revisited a partially initialized module. Normal registry-first startup and the full tests passed. Separate declaration types from eager registry discovery if direct module use is expected; this is maintainability work, not the primary launch blocker.
- `frontier_store.py` is 1,573 lines with 69 function declarations, and materialization runtime is 1,023 lines. Split along existing domain transitions when touching them, preserving transaction/ownership boundaries. Do not introduce a generic workflow engine or new services to reduce file length.
- The two assistant routes duplicate body reading, admission, concurrency checks, deadlines, and error handling. A small bounded-body helper and consistent request-lifecycle handling are justified. Their limits are separate: each route allows two active requests per process, so the combined assistant capacity is four, not two.
- Public validation parses SQL more than once, and history parses again. The page-local follow validator appropriately has a different access contract. Share parsing utilities only where semantics match; merging the security policies would be risky. Bound analytics work on rejected requests as well as successful execution.

## 2. Catalogue safety against malicious actors

### What already works in the design

The public service token has an explicit endpoint allowlist. Administrative mutations and writable SQL require the admin role. The query process receives reader credentials rather than the control DB/NATS/repository writer credentials; it attaches the lake read-only, disables general external access and extension autoloading, and locks configuration. The Compose reader-bootstrap path and Helm reader-secret separation exist. Public requests cannot choose administrative priority or retain an administrative request class.

Immutable content hashes, conflicting-evidence rejection, transactional applied markers, and commit-before-ACK are valuable integrity protections. Acquisition sharing preserves separate request lineage. Retention is a separately authorized path with snapshot/replay fencing. The audit found no direct anonymous DELETE/DROP route through the public website. These observations are not proof against every DuckDB/parser/runtime exploit.

### F5 — P1 deployment gate: isolate browser egress from the LAN

`crawl/acquisition/destination.py` validates the initial destination's DNS addresses. It explicitly delegates redirects, subresources, alternate browser targets, and DNS rebinding to the CDP deployment. A public page can subsequently cause different network requests; initial DNS validation cannot enforce the whole browser session.

For this LAN deployment, prove the browser cannot reach the admin UI, databases, NATS, object-store administrative surfaces, host services, loopback/link-local destinations, or internal DNS destinations. Test the policy with an isolated benign internal target, including redirect, subresource, IPv6, and rebinding cases. Do not aim such tests at actual destructive admin operations.

Also restrict the public application to the internal services it needs. The checked-in Compose file puts services on the same default network, and the admin nginx proxy injects its admin bearer token for `/api/` requests. A process with network access to that proxy does not need to know the token. This is a significant pivot **if** a public-process compromise or browser SSRF occurs; no such compromise was demonstrated.

### F6 — P1, code-confirmed: bytes and parse complexity are not bounded on native capture

`crawl/acquisition/capture.py:132-142` obtains and decodes complete response bodies. HTML capture similarly retrieves the full DOM. `crawl/acquisition/service.py:112-121` computes identities without a native-capture byte ceiling. The optional `maximum_bytes` in the object helper is used by external imports, not this capture path.

`materialization/document_projection.py:61-75` fully reads/decompresses each source and materializes all element rows. Its `content_bytes` field is not an admission limit. Batches are bounded by visits, not retained bytes, element count, parse complexity, or cumulative Arrow allocation. DuckDB memory settings do not bound Python/html5lib/Arrow allocations.

An attacker can use a modest number of large or pathological pages to exhaust crawler/materializer memory or keep a poison batch failing. Put byte limits at acquisition and bounded parse limits at projection, account for decompression and batch bytes, and provide a bounded failure outcome. Use process-level memory/CPU isolation; a cancellation of `asyncio.to_thread` does not forcibly stop the thread's parser work. Validate with controlled finite fixtures, not host-crashing payloads.

### F7 — P1 for anonymous crawl policy: storage abuse and permanent retention

Default public choices allow 1,000 pages per collection, six submissions per minute, and `retention_seconds = null` (forever). These authorize up to 6,000 requested page units per minute; **they do not imply 6,000 actual captures per minute**. Frontier dispatch rate, collection/interest capacity, and cumulative attempt/time allowances independently cap physical work.

Those controls are useful, but they neither provide per-caller fairness nor bound bytes per capture. One actor can occupy global capacity, consume cumulative allowances, and create indefinitely protected spam. Once an operator replenishes allowances, the storage risk resumes. Default retention is disabled operationally, and even purge mode cannot reclaim forever-protected observations merely because disk space is low.

For research preview, use small public page choices, finite public retention options, and a deliberately small global crawl allowance. Pair these with verified storage alerts and an explicit policy for removal of abuse. Current retention is expiry-based, not a general abuse-takedown workflow; do not improvise raw object deletion around its integrity guarantees. Content hashing prevents byte substitution, not truthful-looking spam or malicious instructions inside correctly captured content.

### Recovery is an unverified launch gate

No tested catalogue backup/restore runbook was found in the repository. Observability documentation delegates backups and their alerts to the platform. This is not evidence that the owner's backups are absent.

Before treating the catalogue as durable, restore an isolated copy from the actual backup system: DuckLake metadata plus required lake files, retained raw objects, and control state as appropriate. Verify public queries and raw-content hashes. A second copy of raw HTML alone cannot restore a lost DuckLake catalogue. Establish compatible retention of backups/snapshots/objects and protection from the same credentials that can delete live data. Record acceptable data loss and recovery time.

## 3. DDoS and resource-exhaustion readiness

Cloudflare Tunnel removes the need for an inbound origin port and routes the public hostname through Cloudflare protection. It does not establish a suitable budget for valid-looking analytical requests. Verify the actual rules and product entitlements, rather than assuming a tunnel supplies application-specific quotas. See [Cloudflare Tunnel](https://developers.cloudflare.com/tunnel/) and [rate-limiting guidance](https://developers.cloudflare.com/waf/rate-limiting-rules/best-practices/).

### F8 — P1, code-confirmed: preparation bypasses the request budget

`periplus-public/src/server/query-proxy.ts:9` passes `consume = false` for prep. `operations/access/service.py:51` returns before checking the consumed-window count when consumption is false. Thus preparation checks the feature switch but does not enforce the SQL rate limit, including when execution capacity for that window is exhausted.

Prep still parses, binds, obtains a snapshot, produces a plan, and occupies the query process's sole slot. The API also locks the shared access row for each prep policy check. Automated prep traffic can deny service to ordinary SQL users and corpus-seed work without consuming the advertised SQL request quota. Give prep its own bounded budget or charge a shared operation budget; protect it at the public ingress as well.

### F9 — P1/P2 depending on scale, code-confirmed: uncached browsing queries share scarce SQL capacity

`periplus-public/src/app/api/catalogue/capture-count/route.ts` executes `count(*)` through the query service on every request, with `cache: "no-store"`, and no `admitPublic` call. It runs even when the public SQL feature is disabled. The application may deliberately allow fixed browsing reads while SQL is disabled, but they still need a resource budget.

Flooding this GET endpoint competes for the same single query slot. Other collection/frontier GET proxies similarly require edge budgets, although the history layer already has eight pending reads and a two-second admission wait, so it is not an unlimited history-work queue.

Cache/coalesce the fixed count with a short freshness interval, and separately budget fixed public reads. Do not require a new durable aggregate or service for a count cache.

### F10 — P1 containment requirement: result limits are not peak-memory limits

`query/service.py:189-194` fetches a complete row, converts its values, and JSON-serializes them before testing the result-byte limit. A single value can exceed the returned result budget before being dropped. A safe local test accepted `SELECT repeat('x', 10000000)` and fetched a 10,000,000-byte cell, above the default 8 MiB result cap. This did not run against production and did not attempt an OOM.

DuckDB's timer interrupts engine operations, not arbitrary Python parsing/serialization. SQL preparation can also evaluate expressions during optimization. The query container's 1 GiB Compose limit and Helm resource limits provide essential blast-radius containment, but a hostile query can still kill that process or monopolize it until the deadline. [DuckDB documents memory outside its buffer manager](https://duckdb.org/docs/current/guides/performance/oom) and recommends [sandboxing untrusted SQL](https://duckdb.org/docs/current/operations_manual/securing_duckdb/overview).

Validate bounded recovery after query process death, enforce engine and whole-process budgets, and consider enforceable limits on pathological scalar/nested results. Keep the outer LIMIT; do not describe it as a computation or peak-memory bound. With the checked-in Compose configuration, public/API/crawler/materializer processes do not have the query process's explicit container resource caps; Helm does define workload resource limits. Actual production topology remains to be confirmed.

### F11 — P2, code-confirmed: body admission happens before assistant capacity is reserved

Both assistant routes read request bodies before reserving an active slot, and their explicit abort deadlines begin afterward. Length is bounded, but the number and duration of partially delivered bodies are not bounded by these route counters. `maxDuration` exports alone are not proof of a hard timeout in the self-hosted standalone deployment.

Use ingress/body timeouts and a bounded request-body admission stage. Keep the existing in-flight checks and global assistant quota. Model token/step limits and timeouts exist, but they are not a monetary daily budget; verify provider spending limits for anonymous usage.

### Edge acceptance for this deployment

Configure and test distinct rate policies for SQL exec, SQL prep, both assistants, collection submission, and fixed browsing GETs. Reject excessive request bodies early. Keep static assets cacheable. Choose limits against the actual single-query capacity and intended preview audience; arbitrary large requests-per-second numbers are not meaningful here.

Global feature counters protect total expenditure but are easy for one actor to exhaust. Add an appropriate edge caller/bot control; avoid making IP alone a permanent identity. Verify direct origin access is closed and only the intended public hostname/routes enter the tunnel. Use synthetic public SQL and materialization-progress checks, not just process health. Verify alert delivery for saturation, CDC lag, failed batches, low disk space, and depleted crawl allowances.

## 4. Private schema feedback

Here “private” means internal control/storage contracts, not private user datasets. The product currently shares all request classes' evidence.

**Keep the authority split.** Postgres for editable intent/frontier, NATS for delivery/leases, immutable objects for bytes, and DuckLake for evidence and projections is coherent. Moving crawl history back into Postgres or adding another execution ledger would increase failure modes. Postgres uniqueness on collection URL interests and pending acquisitions is valuable; preserve it during refactors.

**The evidence grains are good.** Separate visit, attempt, step, document, fulfillment, and acquisition reason distinguish what happened from why it happened. A nullable finish time for an uncertain attempt avoids fabricated certainty. The bidirectional visit/document references require validation, but their integrity checks are preferable to collapsing evidence into one wide row.

**Fix semantic versioning before extending the schema.** F2 means content identity alone does not identify the interpretation used to build a generation. Pin parser semantics and preserve meaningful detector provenance. The media detector returns name/version/confidence, while the physical document contract exposes media type/charset without those detector fields; decide whether cross-release reproducibility needs them. This is schema feedback, not a reproduced corruption claim.

**JSON is appropriate for frozen policy snapshots; operational keys should stay typed.** Keep searchable status, identities, budgets, leases, and timestamps as typed columns. Do not duplicate all snapshot fields into a generic entity model. Validate invariants at ingestion because lake relations do not automatically supply all relational uniqueness/foreign-key guarantees.

**Audit private history growth.** Query history keeps SQL, parameters, a template, and plans for 30 days. Delivery concurrency is bounded, but sustained permitted/rejected traffic can still grow control storage. Monitor bytes and prune with tested indexes/batches. Keep that data private and out of ordinary telemetry. The current retention of execution text is already disclosed; full backup retention and the plan-recording scope need operational clarity.

**Retention receipts are justified complexity.** Do not delete replay fences to make the schema look simpler. Their unbounded lifetime is a real growth tradeoff: future compaction needs an explicit maximum replay horizon, as the retention design already acknowledges.

**Physical performance candidates require plans.** Visits partition by `finished_at`, while users commonly filter `observed_at`; document lookup joins use visit/document IDs while documents are bucketed by content hash. These are schema/layout candidates to benchmark, not proven performance bugs. No representative production-scale EXPLAIN evidence was collected, so changing partitions now would be premature.

## 5. Public schema feedback

**Keep `web` contextual and `content` content-addressed.** The split is understandable and prevents identical bytes at multiple URLs from duplicating structural content. Keeping resolved link occurrences at observation grain is correct because base URLs differ. Keeping lineage separate avoids accidental row multiplication. Do not add a mandatory latest-page dimension or a domain-entity schema for preview.

**Add an accessible terminal timestamp for failed observations.** Public `web.observation` includes failures but exposes only `observed_at`, which is nullable when no content was captured; private `finished_at` is always available. Users cannot reliably time-filter/order all failures or compute recent failure rates. Exposing a terminal timestamp would improve the evidence kernel without introducing a derived state table.

**Make projection readiness available to SQL consumers.** API detail exposes readiness, but the public SQL interface does not distinguish pending/unavailable HTML projection from absent structure. An inner join to `content.html_element` can silently exclude newly captured HTML. Provide a documented public readiness/status relation or equivalent explicit contract, rather than having every client infer it from an empty join. Preserve the narrow observation view.

**Reconsider the opaque collection specification boundary.** `web.collection.specification` publishes the frozen internal execution structure, including submitted SQL/parameters and request origin. Shared publication is intentional, but it couples public consumers to internal JSON shape. Promote fields with active query callers to documented typed public columns and document what remains in the snapshot. Do not mistake LAN-only admin access for private collection intent.

**Define content metadata conflict semantics.** `content.object` groups documents by hash and chooses independent `min` values for media type, charset, and size. Byte length should be invariant and detector metadata should ideally be invariant under a pinned interpretation. If detector results differ across versions/import paths, independent minima can combine metadata no single observation supplied. Validate invariants or define a coherent canonical choice; do not silently rely on lexical minima as the interpretation contract.

**Performance triage, as required by QUERY.md:** the retained-reference semijoins and content aggregation in `content.object`/`content.html_element` are schema/catalogue work inherent in these views. Whether selective content-ID queries avoid broad scans is a compiler/optimizer question. A slow query here may be **both**, but that classification requires representative scale and EXPLAIN/ANALYZE evidence. No broad-scan performance failure was measured in this audit. Avoid preemptive rewrites or duplicate tables to compensate for an unexamined plan.

**Keep the structural text helper honest.** Explicit subtree bounds, max elements, truncation, and DOM text order are good contracts. Script/style text and non-visual whitespace are intentionally retained. Do not present it as cleaned article text. Expand format helpers only for active callers, after hostile-input handling is fixed.

**Clarify portability.** Direct DuckDB documentation recommends three-part names such as `periplus.web.observation`; the HTTP validator rejects all explicit catalogue names. That difference is reproducible and may be intentional. Explain it clearly in service/SDK examples or accept only the configured catalogue qualifier. It is a usability inconsistency, not a demonstrated privilege bypass.

## Verification and remaining acceptance

`make check` completed successfully during this audit:

- Backend: 570 tests ran, 33 skipped.
- Python SDK: 5 tests passed.
- Console core: 29 tests; terminal shell: 2 tests.
- Public: 40 tests, typecheck, lint, and production build passed.
- Admin: 12 tests, typecheck, lint, and production build passed.
- `npm audit --omit=dev`: zero known advisories at audit time. This is not a Python dependency vulnerability audit or a guarantee against unknown flaws.

Logs: `/tmp/periplus-readiness-check.log` and `/tmp/periplus-readiness-npm-audit.json`. Reproductions used local Python/in-memory fixtures only. The initial direct projection import encountered the import-order coupling noted above; the hostile JSON-LD reproduction was then run through registry-first initialization successfully.

Skipped coverage includes opt-in live Postgres races and browser checks; the passing suite does not replace them. No full CDP → ingestion → CDC → public-query run, outage/load test, actual Cloudflare rule test, restore drill, or production-scale query benchmark was performed. Existing historical acceptance records are not fresh evidence for this uncommitted working tree.

Before opening the affected preview capabilities: resolve F1/F2; establish browser/LAN containment; bound capture and projection resources; close prep/fixed-read admission gaps; choose finite preview crawl/storage budgets; verify backups and alerts; test the exact release through the tunnel. Re-run the skipped targeted integration tests on isolated services and one low-depth, low-concurrency crawl after fixes. Publish from a tested immutable revision. Formal privacy/data-use terms and an actionable removal process remain owner decisions explicitly unfinished in the public About page; this audit makes no legal compliance determination.
