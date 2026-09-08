# Security

Report suspected vulnerabilities privately to **ekku.leivonen@elei.io**. Include
the affected revision or deployment, reproduction steps, impact, and a minimal
proof of concept. Do not include real credentials or collected personal data.
Do not open a public issue for an undisclosed vulnerability.

Periplus is in research preview. Security fixes target the current main branch;
older revisions do not have a maintenance or response-time guarantee.

## Deployment boundaries

- Public crawl submissions and their evidence are shared. Do not submit secrets
  in URLs, descriptions, or request parameters.
- The admin application has no built-in login. Production must protect the UI
  and every proxied API route with the configured access gateway.
- Control, query, Postgres, NATS, object-storage, and CDP endpoints belong on
  private networks. Service credentials belong only in server-side processes.
- Crawl preflight rejects non-public destination addresses. The CDP service
  must enforce network egress restrictions for redirects, browser subresources,
  and DNS rebinding. Preflight is not a browser network sandbox.
- Public ingress must enforce aggregate traffic and body limits in addition to
  Periplus's admission, crawl-budget, and query-execution limits.
- Captured HTML is untrusted content. Serve it using the content endpoint's
  download and isolation headers; do not render it as trusted application HTML.

See [deployment](docs/DEPLOYMENT.md), [access](docs/ACCESS.md), and
[query](docs/QUERY.md) for the implemented boundaries.
