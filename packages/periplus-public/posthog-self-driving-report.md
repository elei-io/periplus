# PostHog Self-driving setup report

## Summary

PostHog Self-driving is configured for this web application: Session Replay, Error Tracking, and Support are on, and health, error, and support signal sources are enabled. A selective five-scout troop and two Replay Vision monitors now feed the Self-driving inbox. Findings should begin appearing in the [Self-driving inbox](https://us.posthog.com/project/601557/inbox) within about 30 minutes.

## AI data processing

Approved by the wizard’s organization-level gate before this setup ran.

## GitHub

The PostHog GitHub App was already connected during preflight. No GitHub Issues responder was enabled because no connected-tool source was selected in this run.

## Products enabled

| Product | Result | Notes |
|---|---|---|
| Session Replay | Already enabled | This is a web app and the existing `posthog.init` configuration does not disable recording. |
| Error Tracking | Enabled | The existing client initialization has exception capture enabled. |
| Support | Enabled | Tickets will arrive only after an inbound email, inbox, or Slack channel is connected in PostHog. |

## Signal sources

| Source product | Source type | Action |
|---|---|---|
| `signals_scout` | `cross_source_issue` | No row created; scout findings are enabled by default. |
| `health_checks` | `health_issue` | Enabled (source id `01a086da-dda5-7535-86c3-e8db40d5a3aa`). |
| `error_tracking` | `issue_created` | Enabled (source id `01a086da-dd64-794b-bf75-698b1ebf34ff`). |
| `error_tracking` | `issue_reopened` | Enabled (source id `01a086da-ddb3-7551-900b-882fc4da56ef`). |
| `error_tracking` | `issue_spiking` | Enabled (source id `01a086da-dd6e-7d24-ba39-53253b4844ad`). |
| `conversations` | `ticket` | Enabled (source id `01a086da-de19-744d-b008-a1bde11746c2`). |
| `session_replay` | `session_analysis_cluster` | Deliberately skipped; Replay Vision scanners are the active replay route. |
| `replay_vision` | — | Deliberately skipped; each scanner’s `emits_signals` setting is its source authorization. |

## Connected tools

No connected tools were selected; the integration picker was dismissed. No external-tool responders were added.

## Scout troop

**Enabled (5):**

| Scout | Why it is enabled |
|---|---|
| `signals-scout-general` | Covers cross-product patterns and otherwise-uncovered surfaces. |
| `signals-scout-product-analytics` | The application captures structured product interactions across discovery, query, export, and collection workflows. |
| `signals-scout-web-analytics` | This is a public web application with marketing and product routes. |
| `signals-scout-observability-gaps` | Identifies important captured events that lack durable insight, alert, or dashboard coverage. |
| `signals-scout-health-checks` | Prioritizes actionable PostHog setup health issues. |

**Disabled (22):**

| Scout | Why it remains disabled |
|---|---|
| `signals-scout-ai-observability` | The app uses an AI SDK, but no PostHog AI-observability evidence was found. |
| `signals-scout-anomaly-detection` | No saved dashboard or insight usage was available to rank it above the chosen specialists. |
| `signals-scout-apm` | No APM or OpenTelemetry usage was found. |
| `signals-scout-conversations` | Support is routed through the native ticket responder. |
| `signals-scout-csp-violations` | No CSP reporting endpoint was configured. |
| `signals-scout-customer-analytics` | No account or group analytics usage was found. |
| `signals-scout-data-pipelines` | No CDP, batch export, or Hog Flow usage was found. |
| `signals-scout-data-warehouse` | No warehouse sources were configured. |
| `signals-scout-error-tracking` | Error tracking is covered by the three native error responders. |
| `signals-scout-experiments` | No active experiment usage was found. |
| `signals-scout-feature-flags` | No feature-flag usage was found. |
| `signals-scout-inbox-validation` | This is a fresh inbox setup with no resolved reports to re-measure. |
| `signals-scout-insight-alerts` | No alert usage was available to rank it above the chosen specialists. |
| `signals-scout-logs` | No PostHog Logs product usage was found. |
| `signals-scout-mcp-tool-calls` | No evidence indicated this project is collecting MCP tool-call telemetry. |
| `signals-scout-replay-vision` | Replay monitors were just created; there are no accumulated observations yet. |
| `signals-scout-revenue-analytics` | No payment or revenue analytics integration was found. |
| `signals-scout-session-replay` | Session replay is covered by the dedicated Replay Vision monitors. |
| `signals-scout-skills-store` | No skills-store usage was found beyond the setup tooling. |
| `signals-scout-surveys` | No surveys exist. |
| `signals-scout-tasks` | No PostHog Tasks usage was found. |
| `signals-scout-web-vitals` | Web-vitals evidence was not available to rank it above the selected web-analytics scout. |

**Run budget:** 100 runs per day; 0 used and 100 remaining at setup time. The current announcement says scouts are in early access and the PostHog Self-driving team can raise the allocation if needed.

## Custom scouts

No custom scouts were created. Two product-specific candidates were proposed: dataset-building journey drop-offs and query-to-export usefulness; the proposal was dismissed.

The request-collection lifecycle was considered but ruled out because telemetry currently records submission without a matching captured completion/failure outcome. Assistant-result quality was also ruled out because it has no durable success signal suitable for a low-noise scheduled check. If a custom scout ever becomes noisy, set `emit: false` on its configuration to keep it in dry-run mode.

## Replay Vision scanners

A scanner is an LLM that watches individual session recordings on a schedule and pushes confirmed findings to the inbox. These are the only components in this setup that spend Replay Vision quota. Findings carry half weight, so corroboration is required before a report is promoted.

| Scanner | Status | Watches | Query scope | Sampling | Estimate |
|---|---|---|---|---|---|
| Dataset discovery breakage monitor | Created | Visible broken or blocking experiences in the app’s primary dataset-discovery completion flow. | Recordings that visited `/discover`. | Focused, 10% | 0 matched sessions in the one-day estimation window; 0 observations and 0 credits/month currently projected. |
| Interaction frustration monitor | Created | Clearly visible unresponsive controls or other material frustration. | Recordings containing a rage-click event. | Focused, 10% | 0 matched sessions in the one-day estimation window; 0 observations and 0 credits/month currently projected. |

The Replay Vision organization quota had 2,500 credits remaining and was not exhausted at setup time. There are no recordings yet, so both monitors are armed and will begin operating when recordings arrive.

## Follow-ups

- [ ] Connect an inbound Support channel (email, inbox, or Slack) in PostHog so the enabled ticket responder can receive tickets.
- [ ] If product usage expands, enable the relevant disabled scout from the inbox (for example, AI observability, feature flags, surveys, revenue, or web vitals).
- [ ] Re-authorize the PostHog MCP connection with property-definition read access if schema-level verification of Replay event filters is needed.

## What happens next

Fresh scout configurations are picked up by the coordinator within roughly 30 minutes and consume the shared daily scout budget. Replay Vision monitors will begin scanning as recordings arrive. Findings are clustered into reports in the Self-driving inbox, where immediately actionable items can begin coding tasks.

## Files modified or created

- Created `posthog-self-driving-report.md`.
- No application source files were changed.
