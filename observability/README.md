# Periplus telemetry

Import grafana-dashboard.json and load alerts.yaml into the existing platform's
Grafana/Prometheus stack. These are repository-owned assets, not a new monitoring
service. Alert thresholds are starting points to tune against preview traffic.
Public access controls are independent and unchanged.

## Collection

Scrape each worker pod's metrics port separately (crawler 9090, ingestor 9091,
materializer 9093, janitor 9094), API /metrics and query /metrics on their private
HTTP ports. Use Kubernetes endpoint/pod discovery, not a load-balanced service IP.
Query /metrics is unauthenticated like API metrics: never route it through public
ingress. Public /api/metrics requires the existing query-service Bearer credential;
configure that credential from a collector secret, and keep the endpoint private.
It provides process-local assistant/proxy counters and token measurements.

Public token usage is provider-reported, not a bill or a count of people. Proxy
latency includes streaming the upstream response body.
Instrument ingress independently for response bytes and connection-level failures.

Shared queue gauges are repeated by replicas: use max, not sum, and reject samples
whose observation timestamp is stale. The supplied queries assume one Periplus
installation per datasource; add deployment scoping before combining installations.
Rebuild progress is explicitly the last locally observed value and includes an
observation timestamp. It must not be treated as current authoritative fleet state.

## Logs and privacy

Python emits JSON to stderr. Loki should parse each line as JSON, and attach only
bounded deployment/service/level labels. IDs remain searchable JSON fields, never
stream labels. Do not collect request bodies, SQL, parameters, prompts, responses,
Authorization/Cookie headers, URL query strings or native exception text.
Python logs retain static event templates and safe exception type/frame metadata;
legacy interpolation arguments are deliberately omitted. Third-party messages are
replaced by library_event. Migrate important legacy records to explicit safe fields
when more correlation is needed. Admin nginx logs method/status/duration only and suppresses request-context error logs that can echo query strings. Upstream application diagnostics remain available.

Public application events contain only a generated operation ID, operation,
outcome and elapsed time. Deployment-owned Next.js/ingress stderr and request logs
must also be checked for framework-generated native errors; app sanitization does
not govern external log sources. Configure retention/access in the platform.

## Health and acceptance

Workers expose /livez for event-loop liveness and /healthz for dependency readiness.
Health responses contain no native errors. Scrape success alone is not readiness.
The public health endpoint is process-only. Use an external synthetic read-only SQL
probe for end-to-end availability. Never send credentials or SQL in a probe URL.

Before rollout: validate rules with promtool; import the dashboard; confirm every
replica is a distinct target; verify missing-target alerts in your platform; route
Grafana notifications to your actual contact point. Test dependency loss, query
saturation, stream failure, malformed input and a safe SQL request containing a
sentinel sensitive literal. The sentinel must not appear in collected logs.
Check worker liveness remains healthy during a dependency outage.

Infrastructure exporters and alert routing are owned by your platform: NATS,
PostgreSQL, object store/CDP, disk capacity and backup alerts must be configured
there. This repository cannot verify their deployment. No application telemetry
tables, queues or persistent samples are created by this implementation.

Local verification commands:

```
promtool check rules observability/alerts.yaml
cd observability && promtool test rules alerts.test.yaml
```

Committed-output counters exclude both replayed and superseded materialization
batches. Shared queue gauges are current observations; timestamp gauges establish
freshness. Phase durations measure performed work, even if a batch is superseded.

Private SQL history is separate from operational logging. The explicitly authorized
`query_executions` table retains original SQL and parameters for 30 days; see
[QUERY_HISTORY.md](../docs/QUERY_HISTORY.md). Its contents must not be shipped to Loki.
`periplus_query_history_delivery_total` reports stored, failed and dropped remote
records; failures/drops can mean incomplete analytics and have an initial alert.
The existing janitor failure rule also covers the `query_history` cleanup phase.
