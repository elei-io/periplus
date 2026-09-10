# Periplus public

The public story is **the web, as one dataset**: an alternative analytical lens through a unified
tabular representation of HTML structure. Users define the meaning through their queries.
Lead with that perspective, then show concrete views of the same foundation. Collection is an
implementation detail and coverage is inspectable. Keep the whole-web vision distinct from today's
corpus; describe preserved structure accurately rather than claiming byte-for-byte losslessness
of the parsed projection. Source bytes are retained separately.

- `/`: marketing landing page with a Discover launcher and Build/SQL entry points, live book-price example, and concise product story.
- `/about`: vision, worked extraction, join semantics, collection, and preview access/data-use disclosures.
- `/discover`: question-led investigation with source evidence in Findings and optional schema or dataset suggestions.
- `/build`: exact dataset specifications with editable ordered columns, types, nullability, validation, and saved Periplus JSON draft import/export.
- `/sql`: independent SQL workspace with CodeMirror, schema exploration, CSV export, and query share links.
  Home submissions launch once; the URL retains the input but consumes the run flag before execution. Reloading restores a draft.
- `/coverage`: live site counts, distinct URLs, observations, and available collection dates.
- `/datasets` and `/datasets/[slug]`: curated named SQL queries, live previews, source/scope notes, copy/open SQL, and CSV export.
- `/suggest`: collection requests, collection preferences, and public request activity.

Dataset definitions live in `src/lib/datasets.ts`: a name, description, SQL, and explanatory scope/grain metadata. They do not persist result copies or introduce a separate query endpoint. Coverage and dataset previews use the existing Python query API through the Next.js proxy, with React Query caching for one minute in the browser.

The server-side agent uses Vercel AI SDK 7 (`ToolLoopAgent`, typed tools, UI message streams)
and the browser uses `useChat`. It calls Python `/query/exec` for every SQL operation. The agent uses the same checked dataset definitions for matching questions and receives the compact public schema upfront. Exploration is bounded by elapsed time and cumulative output tokens, with a final-query allowance and time reserved for presentation (180 seconds overall, 32 steps as a safety ceiling). The agent reads Python’s `/query/helpers` registry for every request; `/api/query/helpers` exposes the same documentation through the public proxy. Python
owns SQL validation, read-only execution, and resource limits. The agent cannot crawl or write.
Client history is limited to text and never trusted as tool evidence. Sessions live only in the
browser and are lost on reload. Questions and sampled public results go to the model provider.

Completed analyses distinguish query results from agent analysis. Tables and CSV contain server-resolved SQL output; generated summaries and labels appear separately with links to selected evidence. The server rejects unknown or unselected evidence references. Prompt instructions prohibit embedding generated source text or labels into SQL output; this is not a semantic proof of SQL provenance.

Both workspaces use SQL, SUGGEST_SCHEMA, SUGGEST_DATASET, and SUGGEST_COVERAGE_REQUEST.
Discover investigates questions; Build maps available data to an explicit specification.
Successful SQL evidence appears in Findings independently of dataset suggestions. Only
SUGGEST_DATASET creates a dataset preview. Build checks results against ordered columns,
types, nullability, and acceptance checks. Ready results must be nonempty and untruncated,
with no unresolved specification questions or failed checks. These checks do not prove
semantic correctness. Unfinished specifications can be saved and imported as Periplus JSON
drafts. Coverage suggestions prefill requests for review and do not start collection.

The agent chooses methods to fit the task: representative bounded context for extraction, population
aggregates and cohort checks for temporal analysis. The 20-row model evidence cap is not an input
population limit. Precise requests can execute directly; ambiguous ones combine small SQL probes with
focused design questions. There is no forced correction query after every incomplete assessment:
remaining design choices can be resolved with the user. Turn deadlines still bound investigation.

Selected results resolve only to successful queries in the current request. Tables and CSV contain
actual returned rows; exploratory coverage evidence may accompany the working brief without being
called the final dataset. SQL-only requests produce an unexecuted draft. Follow-up requests carry the
brief, assessment and recent SQL as bounded, untrusted text notes, without replaying result payloads.
Corpus claims and prior SQL must be verified again as needed. No extra persistence, agent or service
is introduced; the conversation disappears on reload; exported drafts can be imported again.

Set server-only `OPENAI_API_KEY` and `PERIPLUS_AI_MODEL` to enable the assistant. Existing
`openai:`-prefixed model configuration is accepted. No default model is selected automatically.
The query and collection transports use `PERIPLUS_QUERY_URL` / `PERIPLUS_QUERY_API_TOKEN` and
`PERIPLUS_API_URL` / `PERIPLUS_PUBLIC_API_TOKEN` respectively.

Run `npm run dev -- --port 3011`; the launcher loads the root `.env`.
Run `npm run typecheck`, `npm run lint`, `npm test`, and `npm run build` to validate.
Production ingress owns aggregate traffic limits; the agent bounds concurrent runs locally.

## Product analytics

PostHog starts in `src/instrumentation-client.ts` only when the public token,
host, and `NEXT_PUBLIC_ANALYTICS_ENVIRONMENT=production` are configured at build
time. Local builds do not capture events. Production releases include a Git SHA
and upload source maps through a BuildKit secret.

See the [analytics operating guide](../../docs/ANALYTICS.md) for the event contract,
privacy settings, dashboards, scout thresholds, and weekly review procedure.
Custom events record outcomes, counts, durations and correlation IDs. SQL,
parameters, prompts and result rows are excluded; sensitive workspace regions are
blocked from session replay. Coverage readiness is observed by the submitting
browser and is not an authoritative backend completion metric.

## Collection requests and Live

`/suggest` accepts a starting URL or description, depth 0–2, internal/external/both link scope,
policy-defined page budgets and ten allowed sections. These map directly to CollectionSpec and page-local
follow SQL. Section limits constrain selection, not redirects or subresources. Python validates
and stores intent in Postgres; `/api/collections` proxies the same collection API used by the SDK.

The activity list polls every ten seconds and distinguishes active, paused and settled requests.
Details show discovery, admission backlog, runnable/deferred/unknown queue counts, waiting age,
shared/reused results, last progress and conditional estimate ranges. Frontier items and durable
arrivals link to public provenance. Request settlement does not prove query readiness; the latter
requires a separate catalogue/materialization proof. Historical requests remain readable after
operational cleanup. Public credentials cannot change crawler controls. All request classes share evidence.

`/live` shows bounded current worker/domain activity, recent public captures and upcoming work.
It reports observation time and unavailable/stale dependencies without claiming a global FIFO order.

Analysis allows up to 24 notes of 2,000 characters with a shared 16,000-character prose budget
including the brief and confidence. Oversized prose is rejected, never silently clipped. Returned query rows export as CSV; specifications export separately as JSON drafts.

Public feature availability, rate limits and crawl choices come from `/api/access`.
See [ACCESS.md](../../docs/ACCESS.md) for gates, independent assistant/SQL operation, and cutover.
