# Public product analytics

PostHog project **601557**, US region, measures three distinct public workflows:
Discover investigates questions, Build validates explicit specifications, and SQL
runs direct queries. An answer, evidence, and a validated dataset are different
observations. An answer without a dataset is not a failed Discover turn.

## Dashboards

- [Activation](https://us.posthog.com/project/601557/dashboard/2080576): visitors by
  page and referring domain; Discover question → evidence → completed answer;
  Build validation requested → validated → export; SQL execution → nonempty
  results → export; contextual handoffs; observed coverage readiness. Discover
  output flags overlap and are not a score for answer quality. Visitors by page
  counts every viewed page, not only a session's entry page.
- [Reliability](https://us.posthog.com/project/601557/dashboard/2080578): SQL,
  Discover and Build terminal outcomes, completed-operation p95 duration, first
  evidence latency, validation outcomes, provider token totals, and exceptions.
  Schema mismatch, empty evidence and coverage gaps are not execution failures.
- [Retention](https://us.posthog.com/project/601557/dashboard/2080579): weekly
  returning SQL result users, validated dataset builders and Discover investigators
  receiving evidence. These are anonymous browsers, not accounts. Evidence is
  an engagement proxy, not proof that the user found an answer useful.

All insights filter `environment=production`, `analytics_version=2`, and exclude
Internal / Test users (cohort 560558). Version 2 begins with the new instrumentation
release; earlier data remains intact and is deliberately excluded. Empty charts
before deployment or with little traffic are expected. The starter and wizard
dashboards and their insights were soft-archived, not their underlying events.

Funnels describe browser journeys within one day, not exact-operation joins.
Use `operation_id` or `request_id` to investigate individual attempts. A handoff
click records intent, not successful destination use or conversion. Do not require
Discover users to proceed to Build. Retention needs mature weekly cohorts.

## Event contract

All events include environment, analytics version and release (full Git SHA in CI).
No event includes user SQL, prompt text, schemas or result contents.

| Event | Meaning / properties |
| --- | --- |
| `workspace_turn_started` | Actual submitted turn; `workspace=discover/build`, `operation_id`, entry, `validation_requested` |
| `workspace_evidence_shown` | First successful SQL evidence rendered during the turn, including empty results; operation ID, `has_nonempty_evidence`, `time_to_first_evidence_ms` |
| `workspace_turn_finished` | Terminal `completed/failed/cancelled`; duration, workspace, operation ID, output flags, SQL error count, dataset status, available model/token metadata |
| `dataset_validation_finished` | Requested validation ends: `validated/needs_changes/no_result/failed/cancelled`; operation ID, issue count and returned row count when available |
| `dataset_export_initiated` | Browser CSV download initiated; workspace, validated flag, rows, truncation and originating operation/result IDs |
| `dataset_definition_saved` | JSON definition download initiated; field count and validated flag; operation ID when exporting a result definition |
| `dataset_schema_imported` | JSON schema successfully parsed and accepted; field count |
| `workspace_handoff_opened` | Contextual internal link clicked; bounded source/destination paths and context-presence flags, never URL inputs or link text |
| `site_navigation_opened` | Ordinary internal navigation link clicked; bounded source/destination paths |
| `sql_query_started` | Actual execution starts; operation ID, parameter-presence flag |
| `sql_query_finished` | `success/failed/cancelled`; duration, error category, row count, truncation, query ID and execution time |
| `sql_results_exported` | SQL download initiated or clipboard write succeeds; format, method, rows and originating operation/query IDs |
| `sql_query_shared` | Clipboard write succeeds; operation ID and whether the draft matches a successful nonempty result |
| `coverage_request_submitted` | Submission API succeeds; request ID and bounded settings |
| `coverage_request_ready_observed` | Submitting browser observes readiness; request ID, supplied pages and elapsed time since local submission |
| `coverage_request_settled_observed` | Submitting browser observes settlement; request ID and outcome |

Turn output flags are `has_answer`, `has_evidence`, `has_nonempty_evidence`,
`has_schema`, `has_dataset`, and `has_coverage_suggestion`. They describe output,
not usefulness or correctness. Missing provider usage is unknown, not zero;
tokens are not monetary cost. First-evidence timing comes from streaming state,
not final turn completion. A failed turn can retain successful evidence.

Browser closure, blockers, lost streams and failed delivery can lose events.
Missing terminal events do not prove backend failure. Downloads record initiation,
not proof that a file was saved or used. Coverage receipts are bounded to 50 and
30 days in browser storage; no revisit means no observed readiness event.
Prometheus and server logs remain operational evidence; analytics is not billing
or an authoritative SLO source. Operation IDs are validated UUIDs, not credentials.

## Privacy and internal testing

Only production builds initialize capture. Local builds remain off by default.
URL query strings, fragments and credentials are removed from event URL properties.
Exception messages/source context and structured content properties are redacted.
Generic autocapture, heatmaps and dead-click capture remain disabled.

Replay samples 20% of sessions. Entire conversation, Builder, result, SQL and
coverage-story surfaces are blocked/masked, including contextual links carrying
questions, SQL, drafts or coverage descriptions. Inputs are masked; network
bodies/headers and console recording remain off. Review selectors when adding
surfaces. Replay is for surrounding navigation, not reading user work.

For an internal production browser, visit `/?analytics_test=1` once. The SDK sets
`$internal_or_test_user=true` on events and that anonymous person. Project filters
exclude the event flag directly (including the first personless pageview) and the
person flag, alongside the existing exclusion cohort. This also excludes their later visits until
browser identity is reset. Use it in each test browser/device; the query parameter
is not sent as a URL property. The cohort also matches identified `@elei.io` users.

## Scouts

The three existing daily read-only scouts remain: SQL reliability, workspace
quality (`periplus-discovery-quality`), and activation/coverage. The workspace
scout evaluates Discover and Build separately. It never flags fewer datasets per
Discover turn, coverage gaps, or schema mismatches as execution failures.

All use version 2 production data and exclude internal/test users. They compare
a complete UTC day with seven prior complete days only after eight days contain
the new contract; require 20 relevant operations per window and five affected
browsers; deduplicate; and emit at most one finding per run. Insufficient traffic
emits no finding. Failure thresholds remain above 10% and twice baseline. SQL
latency must exceed ten seconds and twice baseline. Export conversion requires
20 eligible browsers in each window and a 20-point drop. Coverage latency requires
20 observed completions per window, above 30 minutes and twice baseline.

Automatic coding remains off, report cap three/day, no external write scopes,
MCP destinations, Slack or webhooks. Broad wizard scouts and replay scanners
remain paused. These limits are not billing caps.

## Release verification and launch review

1. Run typecheck, lint, tests and both frontend builds. Deploy the public image:
   these are build-time settings; changing runtime environment is insufficient.
2. Mark a production test browser using the URL above. Exercise Discover evidence,
   SQL result/export, SQL → Build, and validation/export. Verify named events,
   operation IDs, release, analytics version, and sanitized URLs in PostHog.
3. Verify the internal person joins the exclusion cohort and does not affect the
   dashboards. Check source-map upload in CI. Confirm production traffic separately
   from local validation; do not fabricate capture events to fill dashboards.
4. Review activation and reliability weekly, then retention once cohorts mature.
   Check denominators before prioritizing; low volume cannot establish a regression.

See [deployment](DEPLOYMENT.md#public-analytics) for SDK build inputs and source maps.
