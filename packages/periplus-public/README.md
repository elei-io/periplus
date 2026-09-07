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
- `/suggest`: collection requests, collection preferences, and public request activity.

Dataset definitions live in `src/lib/datasets.ts`: a name, description, SQL, and explanatory scope/grain metadata. They do not persist result copies or introduce a separate query endpoint. Coverage and dataset previews use the existing Python query API through the Next.js proxy, with React Query caching for one minute in the browser.

The server-side agent uses Vercel AI SDK 7 (`ToolLoopAgent`, typed tools, UI message streams)
and the browser uses `useChat`. It calls Python `/query/exec` for every SQL operation. The agent uses the same checked dataset definitions for matching questions and receives the compact public schema upfront. Exploration is bounded by elapsed time and cumulative output tokens, with a final-query allowance and time reserved for presentation (180 seconds overall, 32 steps as a safety ceiling). The agent reads Python’s `/query/helpers` registry for every request; `/api/query/helpers` exposes the same documentation through the public proxy. Python
owns SQL validation, read-only execution, and resource limits. The agent cannot crawl or write.
Client history is limited to text and never trusted as tool evidence. Sessions live only in the
browser and are lost on reload. Questions and sampled public results go to the model provider.

Completed analyses distinguish query results from agent analysis. Tables and CSV contain server-resolved SQL output; generated summaries and labels appear separately with links to selected evidence. The server rejects unknown or unselected evidence references. Prompt instructions prohibit embedding generated source text or labels into SQL output; this is not a semantic proof of SQL provenance.

The presentation tool maintains a dataset brief: intended use, grain, fields, population, time scope,
acceptance criteria and open questions. A turn can remain `designing`, or conclude `ready`,
`collection_needed`, or `not_fit`. Operational failures use `blocked`, never a coverage or fit verdict.
Coverage and correctness confidence are separate low/medium/high assessments with reasons.
Ready requires executed nonempty results, no unresolved brief questions, and no result-budget truncation;
these structural checks do not prove semantic correctness. Collection recommendations require selected
query evidence. Users refine the brief through ordinary follow-up messages, not a separate form.

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
is introduced; the brief disappears with the conversation on reload.

Set server-only `OPENAI_API_KEY` and `PERIPLUS_AI_MODEL` to enable the assistant. Existing
`openai:`-prefixed model configuration is accepted. No default model is selected automatically.
The query and collection transports use `PERIPLUS_QUERY_URL` / `PERIPLUS_QUERY_API_TOKEN` and
`PERIPLUS_API_URL` / `PERIPLUS_PUBLIC_API_TOKEN` respectively.

Run `npm run dev -- --port 3011`; the launcher loads the root `.env`.
Run `npm run typecheck`, `npm run lint`, `npm test`, and `npm run build` to validate.
Production ingress owns aggregate traffic limits; the agent bounds concurrent runs locally.

## Collection requests and Live

`/suggest` accepts a starting URL or description, depth 0–2, internal/external/both link scope,
up to 1,000 pages and ten allowed sections. These map directly to CollectionSpec and page-local
follow SQL. Section limits constrain selection, not redirects or subresources. Python validates
and stores intent in Postgres; `/api/collections` proxies the same collection API used by the SDK.

The activity list polls every ten seconds and distinguishes active, paused and settled requests.
Details show discovery, admission backlog, runnable/deferred/unknown queue counts, waiting age,
shared/reused results, last progress and conditional estimate ranges. Frontier items and durable
arrivals link to public provenance. Request settlement does not prove query readiness; the latter
requires a separate catalogue/materialization proof. Historical requests remain readable after
operational cleanup. Public credentials cannot change crawler controls or private collections.

`/live` shows bounded current worker/domain activity, recent public captures and upcoming work.
It reports observation time and unavailable/stale dependencies without claiming a global FIFO order.

Analysis allows up to 24 notes of 2,000 characters with a shared 16,000-character prose budget
including the brief and confidence. Oversized prose is rejected, never silently clipped. Only CSV
export of returned query rows is currently available.
