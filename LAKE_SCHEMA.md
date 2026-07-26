# Lake schema

The canonical physical DuckLake contract is [`docs_v2/SCHEMA.md`](docs_v2/SCHEMA.md).

Atlas bootstraps and validates exactly the five append-only `ingest.*` relations and four
rebuildable `material.*` relations named there. It does not create compatibility schemas, views,
macros, or user materializations.
