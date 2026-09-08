# Periplus Admin

Operator application for crawler controls, finite collections, policies, worker monitoring,
ingestion status, source documents, and materialization rebuilds.

Run `npm run dev --workspace periplus-admin` from the repository root. Configure
`PERIPLUS_ADMIN_API_TOKEN` and `PERIPLUS_API_URL` in root `.env`. The token is used only by the server-side API proxy; the UI has no built-in login.
Production uses the independently built `docker/admin` image behind Cloudflare Access,
which must protect both the UI and its `/api` routes.

The console lives at `/` and executes unrestricted administrative SQL through
`/api/admin/sql/exec` on `PERIPLUS_API_URL`, using the server-side
`PERIPLUS_ADMIN_API_TOKEN`. Scripts run in one request-scoped transaction and return
the final result. Console history is separate from public SQL history.

Storage at `/data/storage` reads current sizes and retention state without persisting
measurements. See [storage semantics](../../docs/STORAGE.md) for coverage and limits.

`/data/ingestion` shows ingestion delivery, current ingestor lanes and a bounded
preview of retained dead letters. Reads reuse existing API-owned NATS handles;
missing sources remain unknown, and failure previews do not retry jobs.
`/data/materialization` shows the deployed projection registry, batch delivery,
worker capacity and existing rebuild history. Completed rebuilds do not establish
current live CDC coverage. Both pages are read-only and store no new telemetry.

`/observatory/crawler` combines the existing global crawler controls with worker
readiness and the existing public live feed. Pause finishes started captures;
settings use optimistic version checks. Acquisition previews and historical
windows are explicitly public-only, while controls apply to all work. No new
telemetry is persisted. Acquisition details remain at `/frontier/items/:id`.

`/observatory/requests` owns saved crawl configuration. Create a request, open it to
edit intent, run it now, and inspect its schedules and recent retained executions.
`/observatory/schedules` owns cadence, start/stop, execution limits and pause/resume.
Each schedule links to the request whose configuration it runs.
`/observatory/executions` owns individual runs and their frozen intent, progress,
controls and historical results. Run once creates an execution without a saved request.
All three are direct Observatory sidebar items. Backend collection and definition
contracts remain unchanged; no telemetry or new persistence is added.
