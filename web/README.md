# Periplus web

The public Periplus application is a Next.js server and browser client. Product-specific catalogue
queries belong here rather than in the Periplus Python API.

## Query boundary

The browser calls the Next.js API. Server-only code then executes bounded SQL through the generic
Periplus query operation:

```text
browser -> Next.js API -> POST /sql/query -> DuckDB -> DuckLake
```

`PERIPLUS_API_URL` configures the private Periplus API origin and defaults to
`http://127.0.0.1:8000`. It is read only by server code. Do not expose it through a `NEXT_PUBLIC_*`
variable or let browser code call Periplus directly.

Application queries belong under `src/server/queries/`. The narrow transport implementation is
`src/server/periplus-query-client.ts`. It intentionally exposes only `executePeriplusQuery(sql)`;
catalogue inspection uses ordinary SQL through the same operation.

## Development

From the repository root:

```bash
npm run check:web
npm run build:web
```

To run the application from this directory:

```bash
npm run dev
```
