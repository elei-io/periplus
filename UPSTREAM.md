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

No open items yet.

## In progress

No items in progress.

## Released, awaiting Atlas adoption

No releases awaiting adoption.
