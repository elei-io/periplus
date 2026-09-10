# Public product analytics

PostHog project **601557**, US region, measures whether visitors get usable data.
The production browser token is public; personal API keys are build/setup secrets.
Local capture is disabled by default. The current plan permits one project, so
production isolation uses an explicit build environment and event filters.
Older wizard events lack the environment property and are excluded from these views.

## Dashboards and decisions

- [Activation](https://us.posthog.com/project/601557/dashboard/2080576): visits → SQL
  execution → nonempty results → export; discovery start → preview → export;
  coverage submission → observed readiness. Improve the largest evidenced drop.
- [Reliability](https://us.posthog.com/project/601557/dashboard/2080578): SQL and
  discovery outcomes, p95 elapsed time, discovery input/output token usage, and
  browser exceptions by release. Inspect operation IDs before assigning causes.
- [Retention](https://us.posthog.com/project/601557/dashboard/2080579): weekly
  returning successful SQL users, exporters, and discovery result recipients.
  These are anonymous browsers, not accounts; devices and cleared cookies differ.

All three filter `environment=production` and exclude the project's existing
Internal / Test users cohort. Maintain that cohort when testing the live site.
Funnels describe browser journeys within a day, not exact-operation joins.
Use `operation_id` or `request_id` when investigating individual attempts.

## Event contract

Every captured event includes `environment` and `release` (full Git SHA in CI).

| Event | Meaning and useful properties |
| --- | --- |
| `sql_query_started` | Actual SQL execution begins; `operation_id`, `has_parameters` |
| `sql_query_finished` | `success`, `failed` or `cancelled`; duration, error category, row count, truncation, query ID and execution time when available |
| `discovery_started` | One submitted discovery turn; operation ID, sample/build stage and entry point |
| `discovery_result_produced` | First nonempty presentation per assistant message; result ID, operation ID, row count, stage, time to first result |
| `discovery_finished` | Turn finishes; success/sample/draft/blocked/failed/cancelled outcome, duration, model and available provider token totals |
| `sql_results_exported` | Successful browser download initiation; SQL/discovery flow, format, row count and originating operation/result ID |
| `sql_query_shared` | Clipboard write succeeds; operation ID and whether the current draft matches a successful nonempty result |
| `coverage_request_submitted` | Submission API succeeds; request ID and bounded request settings |
| `coverage_request_ready_observed` | Submitting browser observes `query_ready=true`; request ID, supplied pages and elapsed time since local submission |
| `coverage_request_settled_observed` | Submitting browser observes settlement; request ID and outcome |

Pageviews, web performance, browser exceptions and named navigation/example
interactions provide context. Generic autocapture, heatmaps and dead-click capture
are disabled. Analytics failures do not change application results.

Coverage receipts are bounded to 50 requests and 30 days in browser storage. No
browser revisit means no readiness event. Browser closure, blocked analytics,
storage restrictions and failed event delivery can lose events. Do not use these
counts for billing, authoritative service SLOs or proof of backend failure. Existing
Prometheus metrics and structured server logs remain operational evidence.

The browser operation ID is a validated UUID propagated to public query/discovery
server logs. It is not authentication. Query IDs link successful SQL outcomes.
Discovery token totals are usage metadata, not billing cost or full LLM traces;
failed/disconnected streams may not provide final usage.

## Privacy and replay

Only production initializes the SDK. URL queries, fragments and credentials are
removed from analytics URL properties; exception messages and source context are
redacted. Custom properties contain no SQL, prompts, parameters or returned rows.
Inputs are masked, and SQL, discovery, result tables and request stories are blocked
from replay to prevent text and link attributes exposing their contents. Replay
samples 20% of sessions; network bodies/headers and console recording are off.
This intentionally limits replay inside data workspaces. Use outcomes and timing
there, and replay for surrounding navigation. Recheck these selectors when adding
new surfaces that render user data.

## Daily scouts

Three daily read-only scouts are enabled: `periplus-sql-reliability`,
`periplus-discovery-quality`, and `periplus-activation-coverage`. They use production
only, require at least 20 relevant operations and five affected browsers, compare a
complete day against seven prior complete days, deduplicate findings, and emit at
most one actionable finding per run. Insufficient data is logged without alerts.

SQL checks failures above 10% and twice baseline, or p95 above ten seconds and
twice baseline. Discovery checks the same failure threshold, a 20-point fall in
nonempty-result production, and doubled tokens per result. Activation checks a
20-point conversion fall and observed coverage latency above 30 minutes and twice
baseline. Retention requires mature weekly cohorts. These are starting thresholds,
not established SLOs; tune from real traffic after two weeks.

Automatic coding is explicitly off (`signals/config.autostart_enabled=false`).
The project caps generated reports at three per day. Scouts have no extra write
scopes, connected MCP servers, Slack or webhook destinations. The five broad
wizard scouts and two Replay Vision scanners are paused. Native health/error
sources remain available. Report caps and daily schedules are not a dollar cap;
check billing separately before enabling paid scans or raising limits.

## Weekly review and release verification

1. Check the three dashboards, denominators and internal-user exclusions. Read
   representative failed operations and successful journeys before prioritizing.
2. Review scout findings manually. Fix one evidenced bottleneck, then compare the
   following release. Dismiss unsupported findings and tighten their thresholds.
3. Check capture/replay usage and source-map availability. Keep feature flags and
   experiments for a specific test with sufficient traffic, not initial setup.
4. On deployment, run a harmless SQL query in the real browser, export its result,
   and confirm start/finish/export events with matching operation IDs and release.
   Check that URLs have no query string and maps are uploaded but not publicly served.

Source-map CI configuration and immutable image rollout are documented in
[deployment](DEPLOYMENT.md#public-analytics). Build and deploy changes before
expecting new outcome events; a completed wizard is not ingestion verification.
