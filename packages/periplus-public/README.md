# Periplus public application

Next.js application for the shared ClickHouse corpus. Routes include `/`, `/sql`,
`/coverage`, `/frontier/[id]`, `/docs` and `/about`. The SQL workbench discovers the
installed `public_v1` views through the query API and supports results, exports
and a bounded SQL assistant. Coverage submits collections and shows their progress.

The server uses `PERIPLUS_QUERY_URL` / `PERIPLUS_QUERY_API_TOKEN` for SQL and
`PERIPLUS_API_URL` / `PERIPLUS_PUBLIC_API_TOKEN` for collection/control requests.
Browser code receives no service credentials. There is one query endpoint.
`OPENAI_API_KEY` and `PERIPLUS_AI_MODEL` enable the optional SQL assistant.

Use React Query for server state and shared HTTP types in `src/types/`.
Read package `AGENTS.md` before edits. Run `npm run typecheck`, `npm run test`
and `npm run build`. Root `make check` includes the application checks.

Optional analytics uses the configured PostHog build inputs; local analytics is
disabled by default. SQL and parameters may be recorded in private operational
history, while query results are not persisted there. See the root architecture,
query, access and deployment guides for current service contracts.
