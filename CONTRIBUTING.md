# Contributing

Periplus is in research preview. Small, focused fixes and reproducible bug
reports are welcome. Discuss substantial features in an issue before investing
in an implementation; an issue or pull request does not guarantee acceptance
or support. Report security issues through [SECURITY.md](SECURITY.md).

Read [AGENTS.md](AGENTS.md) and the domain documents it identifies before making
changes. Frontend packages also have their own AGENTS.md. Periplus is greenfield:
change contracts directly rather than adding compatibility paths.

Follow the [README](README.md) to install dependencies. Run `make check` before
submitting code changes. Add targeted tests for behavior changes. Crawl changes
also need one low-depth, low-concurrency end-to-end example; state when the
required CDP or storage services were unavailable. Never use production data for
tests or commit captures, local state, generated builds, or credentials.

Describe the problem, resulting behavior, and verification in the pull request.
Keep unrelated refactors out of the change. Contributions must be yours to
submit, preserve third-party notices, and be offered under the applicable
package license described in [LICENSING.md](LICENSING.md). Contributions do not
transfer copyright ownership to the maintainer.
