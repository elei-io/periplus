# Periplus public

The public story is **the web, as one dataset**: an alternative analytical lens through a unified
tabular representation of HTML structure. Users define the meaning through their queries.
Lead with that perspective, then show concrete views of the same foundation. Collection is an
implementation detail and coverage is inspectable. Keep the whole-web vision distinct from today's
corpus; describe preserved structure accurately rather than claiming byte-for-byte losslessness
of the parsed projection. Source bytes are retained separately.

- `/`: marketing landing page with an Ask/SQL launcher, live book-price example, and concise product story.
- `/about`: vision, worked extraction, join semantics, collection, and preview access/data-use disclosures.
- `/discover`: Ask and SQL workspace modes, streamed evidence, CodeMirror, schema exploration, CSV export, and share links.
  Home submissions launch once; the URL retains the input but consumes the run flag before execution. Reloading restores a draft.
- `/coverage`: live site counts, distinct URLs, observations, and available collection dates.
- `/datasets` and `/datasets/[slug]`: curated named SQL queries, live previews, source/scope notes, copy/open SQL, and CSV export.
- `/suggest`: pending coverage requests, collection preferences, and public request activity.

Dataset definitions live in `src/lib/datasets.ts`: a name, description, SQL, and explanatory scope/grain metadata. They do not persist result copies or introduce a separate query endpoint. Coverage and dataset previews use the existing Python query API through the Next.js proxy, with React Query caching for one minute in the browser.

The server-side agent uses Vercel AI SDK 7 (`ToolLoopAgent`, typed tools, UI message streams)
and the browser uses `useChat`. It calls Python `/query/exec` for every SQL operation. The agent uses the same checked dataset definitions for matching questions and receives the compact public schema upfront. Exploration is bounded by elapsed time and cumulative output tokens, with a final-query allowance and time reserved for presentation (180 seconds overall, 32 steps as a safety ceiling). Python
owns SQL validation, read-only execution, and resource limits. The agent cannot crawl or write.
Client history is limited to text and never trusted as tool evidence. Sessions live only in the
browser and are lost on reload. Questions and sampled public results go to the model provider.

Completed analyses show a short finding, selected database results, and an analysis scope note.
The agent selects final results by query ID; the server resolves only successful results from
that request. Tables and scalar values render those rows directly, with CSV export and expandable
SQL/execution details. Schema inspections and exploratory queries remain in query activity.
SQL-only requests produce a clearly labelled, unexecuted draft that opens in the SQL workspace.
Follow-up requests carry recent SQL drafts as bounded, untrusted text notes without replaying result payloads. The agent must execute them again before treating them as evidence.

Set server-only `OPENAI_API_KEY` and `PERIPLUS_AI_MODEL` to enable the assistant. Existing
`openai:`-prefixed model configuration is accepted. No default model is selected automatically.
The query and coverage-request transports use `PERIPLUS_QUERY_URL` / `PERIPLUS_QUERY_API_TOKEN` and
`PERIPLUS_API_URL` / `PERIPLUS_PUBLIC_API_TOKEN` respectively.

Run `npm run dev -- --port 3011`; the launcher loads the root `.env`.
Run `npm run typecheck`, `npm run lint`, `npm test`, and `npm run build` to validate.
Production ingress owns aggregate traffic limits; the agent bounds concurrent runs locally.

## Coverage requests

`/suggest` accepts a starting URL or plain-language description, depth 0–2, internal/external/both
link scope, a budget up to 1,000 pages, and optional allowed URL sections (up to ten exact origins/path prefixes with segment boundaries). Section limits apply to starting pages and followed links; they do not restrict redirects or subresources. Larger options are visible but disabled.
The Python API validates and saves requests as `pending` in the operational Postgres
`coverage_requests` table. Next.js proxies POST/GET `/api/coverage-requests` and
GET `/api/coverage-requests/[id]`; it owns no persistence or scheduling.

The public activity list polls every 10 seconds, supports pagination, and filters pending,
finding sources, collecting, requests needing attention, and requests completed in the last 30 days. Individual request links remain readable
regardless of age. Python automatically resolves descriptions through bounded model/Brave calls and starts an ordinary
crawl run. Request details show starting pages, search queries, and live run counters. Collection
completion does not guarantee that materialization is ready. Public credentials cannot invoke
crawl execution endpoints directly.
