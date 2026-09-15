# Query boundary

Corpus SQL runs on ClickHouse through the isolated query service. The public
namespace is `public_v1`: capture, html_element, link and page. The service obtains
a short-lived publication binding through the authenticated control API and
rewrites only that namespace to the selected build's view database. One request
uses one binding. The reader account has SELECT on approved view databases, no
material-table, Postgres, NATS or raw-store credentials.

Validation admits one read-only statement over the public catalogue and rejects
arbitrary external reads and unsupported namespaces. ClickHouse enforces resource
limits in addition to request row/byte/time limits. Cancellation interrupts only
the query lane's connection; the lane is drained before reuse.

Page-local follow SQL still uses bounded standalone DuckDB over a navigation
package for the current page. It cannot read corpus history. Corpus seed SQL uses
the query service and the same public catalogue.

Classify performance issues as schema/layout, optimizer behavior, or both.
Preserve the original business query as the acceptance case. A rewrite cannot
hide an unsuitable public schema. Use `benchmarks/query/` and record physical
work and resource bounds before claiming a scale improvement. The current HTML
array layout and membership views have not been validated at billion-page scale.
