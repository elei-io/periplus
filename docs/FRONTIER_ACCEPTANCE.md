# Frontier acceptance audit

> Historical frontier design/verification record. Its independent background selection,
> allocation and seen-check paths were removed on 2026-09-08. Current execution ownership
> is described in [ARCHITECTURE.md](ARCHITECTURE.md); recurring exploration uses ordinary
> requests via [SCHEDULES.md](SCHEDULES.md). Background-specific acceptance below records
> the superseded implementation, not current requirements.

This is a current evidence index for the twelve acceptance criteria in [FRONTIER.md](FRONTIER.md).
It does not replace that document's earlier requirements or establish completion by itself.
The chronological implementation ledger contains experiment identities and verification results.
The implementation and controlled-URL local acceptance audit are complete. Deployment follow-ups
and the retrospective design-review sequencing deviation are called out explicitly below.

| Criterion | Current authoritative evidence | Final review |
| --- | --- | --- |
| 1. Shared capture, independent traversal, compatibility, cancellation | Live sharing/reuse and private-isolation experiments; SQL requests c2f3b47b-4811-4d07-9461-ff31b5bd417d and fbee84b5-ea7a-4fd5-bb3b-44e781e09d07 shared a root but only one followed its child. Queued cancellation preserved the other caller. Store tests cover incompatible policy/context keys. | Reviewed capture identity and frozen inputs: normalized URL, complete content-policy snapshot, visibility and access context; private identities additionally include the collection ID. Dispatch freezes reasons and clears pending sharing identity. |
| 2. Independent bounded background, zero, traps, privacy, restart seen | Live continuation produced one query-ready background observation after request settlement; zero allocation prevented dispatch with a pending candidate and allowance. After all prior acquisition rows were retired and workers restarted, IANA was declined as `lake_observation`. Background selection/store tests cover trap and private-parent rejection. | Initial trap/limit source review and contention allocation review complete; see the bounded-window audit in the implementation ledger. |
| 3. Durable bounded admission and fair scheduling | Live SQL checkpoint test admitted one URL while dispatch was paused and retained the second at admission capacity. Store and isolated Postgres tests exercise domain filtering before candidate windows, request scheduling turns, concurrent dispatch, and admission bounds. | Reviewed 70–80 blocked candidates beyond the 64-candidate window, rotation, priority aging, and bounded delivery. Selection/admission priority gap fixed and verified against real Postgres. |
| 4. Retries, crashes, pause/cancellation/deadlines, budgets and observation identity | Live queued cancellation, downstream service outage/restart, and SQL checkpoint restart passed. Store/outbox/capture tests cover fencing, claim expiry, publication failures and charge lifecycle; isolated Postgres tests cover racing commits. | Reviewed atomic outcome/fulfillment/outbox acceptance and ingestion commit → result → ACK ordering. Live SIGKILL and Postgres PubAck/marker rollback cover the uncertain physical and durable publication boundaries; identical evidence replay handles partial batch commits and lost receipts. |
| 5. Seed freezing, local follow SQL, public form parity | Live SQL queries retained source snapshot/query identity; partial admission survived restart and completed with original provenance. Divergent page-local follow SQL produced distinct request outcomes. Public form and API share CollectionSpec; selection tests reject catalogue access from follow SQL. | Reviewed form-to-CollectionSpec mapping, server role/size limits, SDK intent parity, frozen seed checkpoints and page-local SQL restrictions; matching mapping/API/selection tests pass. |
| 6. Durable lineage and separate accounting | Real API/browser drilldown showed two capture causes and three result users. It still worked after all eight then-existing collection rows and five acquisition rows were reclaimed; historical collections remained query-ready. | Reviewed public views and durable lineage readers: observations have no request join; collection, fulfillment and acquisition reasons remain separate public grains. Live reverse/forward links survive operational cleanup; late fulfillment does not insert another observation. |
| 7. Truthful Live/request views and visibility | Deployed UI checks verified current, arrival, and complete-collection readiness together. Reverse lineage privacy and API role tests pass. Live exposes bounded worker observations and stale/unavailable evidence. | Conditional next-start ranges are implemented and live API-verified from three recent matching-policy captures. Range rendering and five-second expiry verified in both deployed frontends using an explicit API fixture; the live rendering probe did not observe a range. Frozen admission backlog, bounded preview, elapsed selection age and unavailable reason are live API/browser-verified. Conditional first-admission ranges are live API-verified; both frontend range/expiry paths are fixture-verified. Collection queue/progress summaries are deployed and real API/SDK/browser-verified in both applications, with scoped counts and polling-stable progress timestamps. |
| 8. Real capture, ingestion, materialization and useful throughput | Installed Python 3.11 SDK smoke and deployed browser checks passed. Later captures materialized through live CDC. Experiments count successful supplied pages separately from physical attempts and retain client capture elapsed time. | Direct read-only lake measurement: 21 successful public observations, 21 successful physical attempts (56,891 measured ms) and one uncertain attempt (15,000 ms bound). Counts exclude private observations and are not a sustained-throughput or provider-billing claim. |
| 9. Direct replacement and coherent documentation | Fresh Alembic/control schema and lake cutover deployed after coordinated disposable reset. Old graph implementation/routes/UI/SDK paths removed. Current architecture/schema/lifecycle/query/deployment docs describe the frontier. | Reviewed active source, API routes, Compose/Helm, SDK and documentation. Corrected stale root/public/admin README descriptions, terminal query port/token instructions and cutoff wording. Old terms remain only in historical notes, explicit absence statements and excluded historical reference material. |
| 10. First context and bounded recent reuse | Store tests assert first committed request context, including later shallower paths; live reuse added no physical capture. Concurrency tests cover navigation retirement versus reuse. | Reviewed admission age measured from successful completion, zero-age fresh requests, current URL/effective-URL exclusions, frozen content/access identity and retained branch navigation. Deduplication precedes later context changes; retirement and reuse serialize on the control transaction. |
| 11. Delayed ingestion, historical-check cleanup race, lake outage | Live downstream outage proved capture settlement independent of ingestion; history-based seen survived actual operational cleanup and restart. Tests cover snapshot fencing, lease expiry and unavailable/malformed historical results. | Reviewed bounded snapshot-labelled historical lookups and serialized check/cleanup/admission transactions. Missing snapshot, failed query, expired claim or changed controls cannot prove absence. Evidence markers remain until receipt and every live-check watermark permits cleanup. |
| 12. Cancellation commit order and bounded uncertain attempts | Isolated Postgres cancellation/dispatch race checks and physical allowance races pass; queued cancellation is live-verified. | Current 29-test PostgreSQL concurrency suite passes, including 12 repeated cancellation/dispatch races, simultaneous physical allowance claims, deadline-after-lock checks and publication replay. Live SIGKILL verifies two physical attempts, one page charge and one terminal observation. |

## Other explicit contract gates

- Provider egress remains unverified. Periplus validates initial public destinations and requires
  interception for configured exclusions. These do not prove independent browser-target,
  subresource, redirect, or DNS-rebinding isolation. Stolosio's SECURITY.md assigns the network
  boundary to deployment isolation. Source inspection found hostname blocklists but no egress
  isolation rules in the inspected Compose/Helm configurations. That does not establish the running
  host firewall state. The user is handling isolation through a scraper VLAN; that deployment
  follow-up is not yet verified. FRONTIER.md requires egress protections without prescribing their mechanism;
  comprehensive deployment verification is an interpretation of that boundary, not a separately
  specified frontier feature or a prerequisite for controlled-URL development tests.
- No automatic refresh, learned ranking, billing, arbitrary graph execution, or capture experiments
  are required for this cutoff; FRONTIER.md explicitly defers them. Do not add these to completion scope.
- `make check` most recently passed 499 backend tests (31 opt-in skips), 23 SDK tests, and both
  frontend checks/builds. Opt-in tests require their separate recorded runs; a skip is not proof.
- The final walkthrough covers named commands, catalogue validation, public schema, boundedness,
  concurrency, discovery and the pre-acceptance contracts in the coverage matrix below.

The named `make catalogue-check` passed against the deployed local lake. Current item waits now
explain domain/global constraints and invalidate stale policy hints; first-admission ranges and collection queue/progress summaries are implemented and verified.

## Coverage of the destination contract before the acceptance list

| Contract section | Current implementation and evidence |
| --- | --- |
| Core concepts and ownership | `frontier_models.py`, `frontier_store.py`, ARCHITECTURE.md and the deployed process roles separate finite interests, physical acquisition and immutable observation. No synthetic background collection exists. |
| Requests/background | `frontier_selection.py`, `background_selection.py`, `background_policy.py` and the store implement bounded candidates, domain diversity, traps, zero allocation and independent background reasons. Criteria 1–3 cover live behavior. |
| Once-per-request/reuse | Unique request URL interests freeze the first context. Capture identity and retained-navigation checks were reviewed; criteria 1 and 10 cover concurrent and live behavior. |
| Durable seen | `background_seen.py` uses bounded public evidence queries; checks and cleanup share the control serialization boundary. Criterion 11 and recorded representative-scale query plans cover absence and performance. |
| Budgets/crashes | Reservations, physical allowances, dispatch generations, immutable outbox evidence and conservative uncertain-cost accounting are covered by criteria 4 and 12. No exactly-once remote-browser claim is made. |
| Evidence/public lineage | Separate physical and public relations retain definition, outcome, observation, fulfillment and causal reasons. Criterion 6 covers cleanup survival and visibility in both directions. |
| Admission/eligibility/priority/capacity | Current controls separately bound retained requests/interests/acquisitions and dispatch. Candidate filtering precedes bounded selection windows; domain permits and process pools remain independent. Criteria 2, 3 and 7 cover allocation and explanation. |
| Runtime/recovery | Existing crawler, ingestor, materializer, query, API and janitor roles remain. Outbox publication and immutable receipt reconciliation preserve accepted work. NATS contracts reconcile at startup, not during polling. No remote lake call runs under a control-state lock. |
| SQL/discovery | Corpus seeds freeze snapshots and candidates; page-local SQL cannot read the lake or external files. Description discovery retains bounded model/search decisions and cannot bypass normal admission. Provider HTTP is fixture-tested; no claim of a live external model/search acceptance run is made. |
| Dynamic controls | Global/domain pause, pacing, concurrency, exclusions, request priority, background share and measurable ceilings are versioned controls. Actor/version attribution is retained. DEPLOYMENT.md explicitly describes finish-started pause and emergency network-stop ownership. |
| Capture quality | Standard CDP remains the boundary. Bounded completion actions retain action/config versions, stopping reasons and elapsed time. Completion measurements now flow into step evidence; supplied counts explicitly do not certify usefulness/completeness. A fresh query-ready capture persisted measurements for all three completion steps, verified directly in DuckLake. |
| Live/request transparency | Bounded current/public reads, latest five public successes, upcoming preview, observation times, stale/unavailable dependencies and separately verified query readiness are implemented. Criterion 7 covers UI/API evidence, including estimate expiry and unknown eligibility. |
| Replacement | Active source/routes, models, queues, SDK, frontends and documentation were reviewed for retired contracts. The coordinated disposable reset and deployed schema are recorded. The scheduler alternative review is retrospective: its requested pre-implementation timing was missed and is not claimed retroactively. |
| Explicit deferrals | In-flight joining, general historical reuse, automatic refresh/ranking, paid billing, capture experiments and arbitrary graph orchestration remain intentionally absent. |

Named development and acceptance commands are recorded in the implementation ledger. Current final
checks cover the Python/SDK suites and both application checks/builds; PostgreSQL races, migration
round trips, isolated Chromium interception and live catalogue validation have separate evidence.
No opt-in skip is treated as a pass. The scraper VLAN is the user-owned deployment follow-up,
separate from completed controlled-URL local acceptance.
