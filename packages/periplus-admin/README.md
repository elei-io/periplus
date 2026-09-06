# Periplus Admin

Operator application for crawl plans, schedules, policies, run/worker monitoring,
ingestion status, source documents, and materialization rebuilds.

Run `npm run dev --workspace periplus-admin` from the repository root. Configure
`PERIPLUS_ADMIN_API_TOKEN` and `PERIPLUS_API_URL` in root `.env`. Log in as `admin`
with the token as password. Production uses the independently built `docker/admin` image.
