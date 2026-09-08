# Public launch audit — 2026-09-08

The public frontend has completed a local production-build SEO, Lighthouse,
accessibility, dependency, and packaging audit with fixes applied. This is not a
production launch sign-off: the real public URL was not supplied, local backend
requests returned 503, and production ingress/account settings were not available.
Existing and concurrent workspace changes are preserved and are not attributed
to this audit.

## Changes made

- Added a validated build-time `PERIPLUS_PUBLIC_ORIGIN`, wired through the public
  Docker image and GitHub Actions repository variable. Static and dynamic routes
  use the same compiled origin; canonical URLs never use request Host headers.
- Added per-page canonical, Open Graph, and Twitter metadata for the six public
  routes; added WebSite JSON-LD on the homepage.
- Added a six-URL sitemap without fabricated modification dates and robots.txt.
  Clean workspace pages are indexable. SQL/parameter/question/request variants
  and transient frontier details are noindex; API responses carry X-Robots-Tag.
- Unconfigured builds are deliberately noindex, with a disallow-all robots file
  and empty sitemap. Configuration instructions are in DEPLOYMENT.md. Preview
  hosts serving an indexable production image still need ingress protection.
- Added a compass favicon and a statically generated 1200 × 630 social image,
  matching the site's existing identity. The image uses no external image API.
- Fixed HTML-illustration accessible names so the visible source text is included.
- Made overflowing read-only SQL examples, the join-key table, the text-placement
  code sample, and the concurrently added SDK example keyboard reachable;
  gave documentation tables accurate names.
  Normal page styling and application behavior are preserved.
- Added `X-Content-Type-Options: nosniff` and
  `Referrer-Policy: strict-origin-when-cross-origin`; disabled X-Powered-By.
- Updated only the affected lockfile resolutions: `js-yaml` 4.3.0 → 4.3.2 and
  `qs` 6.15.3 → 6.16.0. The vulnerable versions were transitive tooling
  dependencies through shadcn, not evidence of a demonstrated public endpoint exploit.
- Removed the Dockerfile copy of an empty, untracked public directory, which is
  absent in a clean checkout. App assets are packaged in the Next.js output.
- Added three SEO regression tests covering origin validation, input-bearing URL
  exclusion, and clean-route/unconfigured-build behavior.

## Lighthouse

Lighthouse 13.4.1, headless installed Chrome, local production builds; default
simulated mobile throttling. Baseline used `next start`; final tests use the
standalone server and packaged static assets, matching the container entrypoint.
The test build used the reserved origin `https://periplus.example` to verify the
configured metadata path. This is a test fixture, not a proposed production domain.
No live production settings or deployment were changed.

The raw HTML/JSON reports and browser checks are in
`/tmp/periplus-launch-audit/`. These are local audit artifacts, not committed assets.
Performance scores vary by machine and run; no field INP, real-user Core Web Vitals,
or production-region latency claim is made. Backend-dependent pages were audited
in their unavailable-service state, not with mocked successful results.

| Route | Baseline mobile performance | Final mobile performance | Accessibility | Best Practices | SEO |
| --- | ---: | ---: | ---: | ---: | ---: |
| Landing | 93 | 97 | 100 | 100 | 100 |
| About | 99 | 97 | 100 | 100 | 100 |
| Documentation | 97 | 95 | 100 | 100 | 100 |
| Discovery | 96 | 94 | 100 | 96 | 100 |
| SQL | 92 | 92 (median) | 100 | 96 | 100 |
| Observatory | 90 | 89 | 100 | 96 | 100 |

Desktop performance was 100 on landing, documentation, and SQL. Landing/docs
scored 100 in all four desktop categories; SQL Best Practices was 96 because the
backend was unavailable. Mobile SQL samples were 82, 93, and 92 (median 92); the
82 run had an unusually long observed paint delay. All samples are retained.

The remaining Best Practices deductions on workspaces are logged backend 503s.
Mobile LCP remained approximately 2.5–3.4 seconds in representative runs; rich
editor/SDK JavaScript and render-blocking assets remain tuning opportunities.
The initial SQL outlier had 4.6-second simulated LCP. These scores do not justify
claiming the real service is fast or healthy before testing its deployed backend.


Lighthouse's SEO and accessibility scores alone did not expose all launch issues:
the baseline already scored 100 for both, despite missing canonical/social metadata
and the keyboard/accessible-name issues found by deeper inspection.

## Additional verification

- Axe-core 4.13.0 via Playwright: zero selected WCAG/best-practice violations across
  all six routes at 390 × 844 in light and dark themes. Documentation was rechecked
  after its final concurrent changes; Lighthouse's accessible-name diagnostic also
  passes. Internal fragment targets, single H1s, page overflow, and client exceptions checked.
- Canonical, robots, Open Graph image, and sitemap output checked on rendered pages.
  Input-bearing variants and observation details checked for noindex. A forged Host
  header did not affect canonical URLs, including Googlebot requests.
- Unconfigured build verified to emit noindex, robots disallow-all, and empty sitemap.
- Unknown URLs return HTTP 404 with noindex. Favicon and social image return 200;
  social image is PNG and was visually inspected. No missing favicon request remains.
- Health response headers checked; unauthenticated metrics request returned 401.
- No references to the server credential settings were found in browser JavaScript.
  This is a targeted bundle inspection, not a comprehensive secret or penetration audit.
- Full `npm audit` and `npm audit --omit=dev`: zero known advisories after updates.
- Public typecheck, all 38 tests, lint and production builds; admin typecheck and build.
  The standalone application was smoke-tested. A full Docker image build/push and
  Kubernetes rollout were not performed.

## Remaining launch gates

| Priority | Gate | Required evidence |
| --- | --- | --- |
| Before indexing | Confirm the production HTTPS origin | Set the `PERIPLUS_PUBLIC_ORIGIN` GitHub repository variable; publish a new image; inspect canonical, social URL, robots.txt and sitemap.xml on that host. A runtime environment change alone does not update static metadata. |
| Before opening public features | Verify healthy deployed dependencies | Access policy, capture feed and query backend return healthy responses; run a bounded SQL query, assistant turn and opt-in small submission through the real public gateway. Local audit requests returned 503. |
| Before launch | Confirm ingress and credential isolation | TLS, HTTP-to-HTTPS and preferred-host redirects, preview protection, private API/query/metrics origins, public rate/body limits, and operator authentication are enforced by the deployed platform. Check streaming timeouts with the assistant. |
| Before launch | Resolve the published policy gaps | The About page explicitly says formal privacy/data-use policies and service terms are not published. Have the owner finalize those statements and documents before representing this as a production service; this audit does not invent policy or legal commitments. |
| Before launch | Confirm support and operations | Test the published contact route, monitoring, alert routing, and failure recovery on the intended deployment. Health alone does not prove all public capabilities work. |
| At launch | Verify search ownership and discovery | Verify the real domain in Search Console, submit its sitemap, inspect key URLs, and check crawler-visible responses. No account actions were taken. |
| After launch | Measure actual user experience | Collect representative field performance and repeat audits with healthy results, slow devices, and real network paths. Local simulated scores are not field Core Web Vitals. |

Strict CSP/HSTS and deployment-wide abuse controls should be verified at the actual
TLS ingress. This change does not claim to enforce them or add a permissive CSP
merely to improve an audit score. Source-defined inline framework/theme scripts
must be accounted for before deploying a strict script policy.

## Reproduction

For a local configured test, build with an explicitly chosen reserved test origin:

```sh
PERIPLUS_PUBLIC_ORIGIN=https://periplus.example npm run build --workspace periplus-public
```

Package `.next/static` into the standalone application's `.next/static`, as the
Dockerfile does, and start its server with a free local `PORT`. Run Lighthouse
13.4.1 against `/`, `/about`, `/docs`, `/discover`, `/sql`, and `/observatory` with
all four categories, then desktop on `/`, `/docs`, and `/sql`. Run the normal
frontend checks and both npm audits. Do not deploy the reserved test origin.

References: [Lighthouse overview](https://developer.chrome.com/docs/lighthouse/overview),
[Google canonical guidance](https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls),
[noindex guidance](https://developers.google.com/search/docs/crawling-indexing/block-indexing),
[sitemap guidance](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap),
[js-yaml advisory](https://github.com/advisories/GHSA-5p4m-2wfm-xmqj),
[qs advisory](https://github.com/advisories/GHSA-4mjr-xmp4-gh2g).
