# Public frontend performance audit — 2026-09-08

Scope: public Next.js routes, client rendering, result tables, editor configuration,
discovery conversation, Observatory, asset delivery, and request scheduling.
Changes preserve the existing CSS, native table layout, query/export contents,
polling cadence, and interaction contracts. Existing and concurrent changes in the
shared checkout are not attributed to this audit.

## Changes

| Area | Change | Reason |
| --- | --- | --- |
| Result tables | Memoize `QueryTable`; classify columns without temporary per-column arrays and stop on the first non-URL | SQL typing and unrelated parent updates no longer rebuild every cell; classification avoids unnecessary scans and allocations |
| Schema explorer | Own filter state in a memoized component with a stable SQL-loading callback | Filtering no longer rerenders results/editor, and typing SQL no longer rebuilds the schema tree and tooltips |
| SQL editor | Memoize editor and reuse immutable extensions/basic setup for editable/read-only modes | Avoid repeatedly reconfiguring CodeMirror while typing or polling |
| Documentation | Route examples through the lazy `SqlExample` boundary, retaining server rendering | Keep the editor implementation out of unrelated route loads/prefetches; retain the same rendered editor |
| Discovery | Stable chat transport and callbacks; memoized previous answers and presentation lookup | Typing a prompt no longer reparses/rerenders unchanged answers and datasets |
| Access state | Return only consumed query fields and stabilize denial callback | Avoid subscribing consumers to every React Query status property through object spread |
| Observatory | Lazy-load the submission form; memoize the public-request list | Avoid downloading form-only controls at initial load and rebuilding requests for capture animation updates |
| Animated count | Stop requesting frames when the displayed target is reached | Remove idle animation frames without changing displayed values |

## Tables and virtualization

The public result limit is 1,000 rows / 8 MiB. Results use native automatic column
sizing and variable-height wrapped cells. The audit retained all rows in the DOM:
conventional windowing would remove content used by browser Find, keyboard link
navigation, text selection, accessibility traversal, and intrinsic column sizing.
Fixed row heights or column widths would also change appearance.

Memoization addresses repeated React work without those regressions. Initial
mount/layout still scales with the returned cells; this is a remaining cost, not a
claim of constant-time rendering. Frontier/request lists already use bounded pages
and the capture presentation has a bounded visible tail.

## Measurement method and limits

Production `next build` served locally, headless Chrome, fresh browser contexts,
1440 × 1000 desktop viewport. Resource totals use `encodedBodySize` for downloaded
JavaScript during the initial 1.8-second observation window, including automatic
prefetches. These are compressed response-body bytes, not raw bundle size or total
page transfer. APIs were mocked; no real queries or crawl submissions were issued.

| Route | Baseline JavaScript | Final JavaScript |
| --- | ---: | ---: |
| Landing | 331,025 B | 192,376 B |
| About | 331,025 B | 192,376 B |
| Documentation | 331,025 B | 333,212 B |
| Discovery | 524,749 B | 389,248 B |

Landing/about decreased about 42%; discovery decreased about 26%. Documentation
still loads its editor when visited. Final SQL JavaScript was 539,914 B versus
446,366 B initially, but the SQL assistant was being added concurrently; that
increase cannot be attributed to this audit. Before that concurrent addition,
the first changed-build SQL measurement was 446,024 B.

The table fixture contained 1,000 rows with numeric IDs, URLs, variable-length
wrapped text, and timestamps. Typing the same 27-character suffix took 1,083 ms in
the baseline and 94–297 ms in the first two changed-build runs. This is Playwright
typing wall time on the local machine, not field INP or a statistically established
speedup. Concurrent build activity makes timing noisy.
The final run measured 160 ms.

The table retained exactly 1,000 rows, 84,634 px scroll height, 896 px scroll width,
and column widths of 100 / 240.75 / 401.25 / 154 px. The captured table viewport was
pixel-identical before/after. Landing, about, and discovery screenshots also matched
in the first comparison. Documentation/schema were concurrently edited, so the
whole-checkout screenshots cannot establish visual equivalence for those routes.

The initial Observatory/frontier baseline fixture incorrectly returned an HTTP 200
error-shaped body. It was corrected to HTTP 503 for final interaction tests. Those
baseline route timings/DOM counts are excluded from performance conclusions.

## Delivery and remaining costs

- Landing/about are prerendered; dynamic workspaces remain dynamic.
- Static assets already use compression and `public, max-age=31536000, immutable`.
- The measured shared font payload was 29,288 bytes; shared CSS was about 21 KB
  compressed. No large raster image payload was found on the landing route.
- Query results are serialized/exported on request; exports still include all rows.
- Client server state remains in React Query. Existing caching, cancellation,
  retry, cooldown, polling and freshness behavior were retained.
- The query proxy already streams upstream bodies rather than buffering a second
  complete response. No backend query/schema/storage behavior was changed here.
- Documentation and the SQL console still need CodeMirror when opened. Discovery
  still needs the AI SDK and Markdown renderer. No feature was removed to lower bytes.
- Production CDN/region latency, real backend query and model response times,
  slow-network/device profiles, and field Core Web Vitals were not measured. Local
  route paint timings must not be presented as production performance guarantees.

## Verification

Browser checks at 1440 × 1000 and 390 × 844 cover schema filtering, loading schema
SQL, 1,000 returned rows, scrolling to the last row, presence of searchable last-row
text, execution-plan switching, CSV containing the last row, opening/closing the
lazy submission form, and read-only documentation examples. No client exceptions
occurred in these interaction runs. Browser Find itself and screen-reader behavior
were preserved by retaining native DOM content, rather than separately certified.

Public typecheck, production build, and targeted lint passed. All 34 public
tests passed in the final test run. Admin typecheck and production build also
passed as required by repository instructions. The latest full lint still reports
11 `react-hooks/refs` errors in the concurrently added SQL-assistant component;
those are outside this audit's edits. Targeted lint on all audit-edited source
files passes.
