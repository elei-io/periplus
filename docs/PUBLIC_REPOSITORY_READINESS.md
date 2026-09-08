# Public repository readiness

Review date: 2026-09-08. Reviewed local HEAD `59e1157`, its proposed readiness
changes, existing uncommitted catalogue work, and the remote branches/tags/PR
heads available during this review. This is a bounded readiness review, not a
security certification or a verification of copyright ownership.

The repository remains **private**. No commits were pushed, remote history was
not rewritten, services were not redeployed, and development volumes were not
reset. GitHub dependency alerts were enabled and verified through the API.

## Prepared

- [x] Root AGPL-3.0-only license and an explicit Apache-2.0 exception for the
  standalone Python SDK; the console and shell packages remain AGPL.
- [x] Copyright notices identify **Ekku Leivonen (elei.io)**. Domain ownership is
  not represented as a separate incorporated copyright owner.
- [x] Python and npm package metadata and distributable license/notice files.
- [x] Known shadcn/ui, Lucide, and Geist notices retained in
  `THIRD_PARTY_NOTICES.md`; Dockerfiles include repository license documents.
- [x] Software rights explicitly separated from third-party captured content.
- [x] Private reporting through `ekku.leivonen@elei.io`, preview support
  expectations, contribution guidance, and deployment security boundaries.
- [x] README public-v1 query examples, shared-data disclosure, local setup and
  CDP prerequisites, configurable public budgets, and honest preview limits.
- [x] SDK instructions no longer direct current public-v1 users to the older
  published 0.2.0 contract.
- [x] Additional `.env.*` files ignored, with `.env.example` retained. The example
  has empty service/provider tokens and explicitly local-only storage credentials.
- [x] CI secret scanner with a checksum-pinned Gitleaks binary; weekly dependency
  update configuration. Existing Action version references are pinned to their
  resolved commit SHAs, and checkout credentials are not persisted.

## Verified

| Check | Evidence and limits |
| --- | --- |
| Local tracked files | Gitleaks 8.30.1 found no secrets in a snapshot of tracked files, including `_web_old_dont_touch/`. Local `.env`, virtual environments, and corpus state were excluded from the publication snapshot. |
| Local and remote history | Gitleaks found no secrets across local refs or a separate remote mirror with PR heads fetched. The remote scan processed 160 commits. A separate exact-value check found none of six configured secrets in 6,524 reachable remote blobs. No history was changed. |
| GitHub material | Reviewed six PR records and their available comments/reviews, 40 completed run-log downloads, and all 19 unexpired artifacts. Expanded ZIP and Docker build-record gzip/tar contents before scanning; no secrets detected. Six expired artifacts were unavailable. No GitHub releases were present. Automated scanning does not establish that every historical detail is suitable for publication. |
| Dependency advisories | npm audit: zero known vulnerabilities. pip-audit: zero known vulnerabilities in 82 locked runtime Python dependencies, none skipped. These are point-in-time advisory checks, not proof of safety. |
| License metadata | npm lock entries and 87 installed backend distributions had license metadata. Some dependencies use LGPL, MPL, or font/content licenses; metadata presence alone does not establish all distribution obligations. |
| Backend tests | `make check` backend phase: 608 tests run, 33 skipped, no failures. Its SDK phase: five tests passed. Skipped integration coverage is not claimed as verified. |
| SDK distribution | Built wheel and source archive include Apache-2.0 and NOTICE. Installed the wheel in a separate Python 3.11 environment; all five SDK tests passed. |
| Core and JS distributions | Core wheel/source archive include AGPL-3.0-only and NOTICE. npm dry-run packages for console core, web shell, and terminal shell include LICENSE and NOTICE. |
| Fresh dependency setup | Separate clone overlaid with proposed files, no `.env`, local dependencies, or sibling extension checkout copied. `uv sync --frozen` and `npm ci --ignore-scripts` succeeded; native rebuild and frontend verification use a separately downloaded, checksum-verified Node 24 runtime. |
| Local catalogue | `make catalogue-check` passed against the existing local deployment. This is not a fresh database bootstrap. |
| One-page public crawl | Request `a64e4105-cdf2-4a45-91b0-2b5bce066dd8` used one example.com seed, depth zero, and no historical reuse. The public policy's smallest budget was five; only one page was acquired. It settled with one supplied page and zero failures. A public-v1 query filtered by that request returned its new capture. The collection readiness field was unknown on the last recorded status response; queryability was verified directly. |
| Public query examples | README sample returned ten rows from the existing corpus. There were transient busy/timeout responses during the review; successful retries do not establish a latency or throughput guarantee. |
| Access boundaries | Unauthenticated control/query service requests returned 401. Public queries against internal schema, external-file functions, and multiple statements returned 422. Three local service tokens were present and distinct; frontend credential references are in server-side code/configuration. An exact-value scan found none of six configured secrets in 96 built browser assets. |
| Deployment manifests | Helm lint and rendering passed. Compose published infrastructure ports are loopback-bound. The current stack was healthy when inventoried. Production gateways and remote browser egress were not tested. |
| GitHub permissions | Repository remains private; Actions default token permission is read-only and cannot approve PRs. PR jobs use hosted runners and do not use `pull_request_target`. Dependency alerts returned 204 after enabling. |

Actionlint 1.7.12 passed for every workflow. Full `make check` passed, including
both frontend typechecks, lint, tests, and production builds (the host shell
used Node 22.22.2). The independent clean Node 24.20.0 run also passed all
console/shell checks and tests, both frontend typechecks/lint/tests, and both
production builds. A final 802-file publication snapshot passed Gitleaks with
zero findings; `git diff --check` also passed.

## Remaining publication decisions and checks

- [ ] Confirm rights to original code and custom Periplus marks, including any
  externally sourced work. Commit authorship and dependency metadata are not
  ownership/assignment evidence.
- [ ] Complete the third-party notice and license-obligation review for compiled
  distributions. The included notices cover known UI/font sources, not every
  transitive dependency or container base-image component.
- [ ] Decide whether to retain history. It contains a personal author email,
  historical infrastructure references, and six old smoke-run JSONL result files
  (commands, timings, URLs, and output-path metadata). No secrets were detected;
  `_web_old_dont_touch/` remains intentionally untouched. A clean initial snapshot
  would be a separate, explicit publication choice.
- [ ] Supply and verify the public hosted-service URL. GitHub had no configured
  public-origin variable at review time; no production URL was invented.
- [ ] Set branch protection/required checks once available. The current private
  repository plan returns 403 for branch protection. Include backend, frontend,
  Helm, and secret-scan checks after they have run remotely.
- [ ] Verify GitHub secret scanning/push protection after publication or an
  appropriate plan change. The local workflow does not enable those GitHub
  features. Private vulnerability reporting returned 404; email is the supported
  reporting channel for now.
- [ ] Add a required reviewer to the PyPI release environment: its current
  protection has a branch/tag policy but no required-reviewer rule. Publish a
  version matching public-v1 when ready, and retain exact corresponding source
  for distributed/hosted versions. No release or source offer was published here.
- [ ] Commit/review the prepared changes and run the updated CI remotely.
- [ ] Explicitly change visibility, then check the repository as an anonymous
  visitor and publish a hosted-product demo.

## Separate hosted-launch checks

Publishing source does not complete these operational checks:

- Verify production admin UI **and** API ingress protection, private service
  reachability, aggregate traffic/body limits, TLS, and actual reader credentials.
- Verify CDP network egress blocks private networks, metadata endpoints,
  redirects, subresources, and DNS rebinding. The existing application preflight
  cannot prove that external enforcement.
- Run a fresh Docker build/bootstrap on an isolated host. The maintained Compose
  deployment has fixed singleton names; this review did not replace the active
  stack or reset its data. The clean-clone check covered dependency setup, not a
  fresh end-to-end deployment.
- Verify hosted corpus access/export/retention terms and source-content rights.
  `LICENSING.md` deliberately does not grant rights to crawled websites.

Raw review logs and downloaded material were kept outside the repository under
`/tmp/periplus-readiness-tools/`; the full local check log is
`/tmp/periplus-public-readiness-check.log`. They are local, temporary evidence,
not publication artifacts. Re-run scans immediately before publishing if the
tree, refs, GitHub discussions, or artifacts change.
