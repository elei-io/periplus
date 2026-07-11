# Upstream DuckLake Feedback

This is Atlas's focused wishlist and issue log for the DuckLake libraries maintained alongside it.
Atlas intentionally dogfoods these packages, so friction found here should improve the shared
library instead of becoming a permanent Atlas-specific workaround.

## Libraries

| Package or extension | Atlas role | Local source |
| --- | --- | --- |
| `ducklake-client` | Typed DuckLake configuration, attachment, schema, transactions, and catalogue access | `/Users/ekku/Code/quack/ducklake-python-client` |
| `ducklake-cdc` | Durable DDL/DML change consumption for publication tables | `/Users/ekku/Code/quack/ducklake-cdc-extension` |
| `ducklake-cdc-client` | Python consumer API for publication CDC, replay, and bootstrap | `/Users/ekku/Code/quack/ducklake-cdc-python-client` |

## How to add an item

Keep requests concrete and evidence-based. Include:

- the affected library;
- the Atlas caller and use case;
- the observed behavior or missing capability;
- the smallest useful upstream contract;
- a reproduction, test, benchmark, or relevant source location;
- whether Atlas is blocked or has temporary local code;
- the upstream issue, pull request, release, and Atlas adoption status when known.

Prefer extending an existing item over creating duplicates. Remove completed items after Atlas uses
the published release; version control retains the history.

## Wishlist

### Pin-install contract for `ducklake-cdc`

- **Atlas caller:** the materialization worker image and its durable CDC cursor.
- **Evidence:** `INSTALL ducklake_cdc FROM community` began returning build
  `ducklake_cdc 723dcf8` while Atlas's image/runtime contract expected `f5d2e37`; the worker correctly
  refused to consume with an unreviewed extension build, but a rebuild cannot reproduce the older
  artifact from the same install command.
- **Smallest useful upstream contract:** a documented immutable release/version install URL (or a
  semver-selectable community artifact) plus a stable semantic version returned alongside the build
  hash.
- **Atlas status:** not blocked; Atlas verifies the currently reviewed published build at startup,
  but the Dockerfile's community install is not reproducible across future releases.

## In progress

No items in progress.

## Released, awaiting Atlas adoption

No releases awaiting adoption.
