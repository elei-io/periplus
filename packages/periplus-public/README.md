# Periplus Public

Public catalogue browsing, bounded SQL, and single-page crawl requests. The Next.js
server calls core with a restricted credential; the browser only calls this application.

Run `npm run dev --workspace periplus-public` from the root. Configure `PERIPLUS_API_URL`,
`PERIPLUS_PUBLIC_API_TOKEN`, and `PERIPLUS_PUBLIC_RECEIPT_SECRET` in root `.env`.
Production uses the standalone Next.js image in `docker/public`.

Requests return a signed seven-day receipt for progress; keep its URL private.
Crawl completion precedes ingestion and catalogue materialization. There is no account
system; anonymous submissions are bounded to ten per minute per application process.
