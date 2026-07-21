# Catalogue fixtures

Atlas setup seeds every source-controlled `*.sql` fixture. User-visible definitions in these
directories go through the same catalogue services used by the UI:

- `table_macros/` — one `CREATE MACRO macros.<filename>(...) AS TABLE (...)` statement;
- `views/` — one virtual `CREATE VIEW views.<filename> AS ...` statement;
- `materialized_views/url/` — one self-contained `CREATE VIEW` statement, activated as an
  ordinary materialization driven by `urls`;
- `materialized_views/document/` — one self-contained `CREATE VIEW` statement, activated as an
  ordinary materialization driven by `documents`;
- `materialized_views/crawl/` — one self-contained `CREATE VIEW` statement, activated as an
  ordinary materialization driven by `crawls`;
- `queries/` — one read-only query, named from the filename.

Materialized-view fixtures use the same control-plane definition and materialization machinery as
user-created views. Setup creates their managed definition; the materialization worker creates and
fully populates the backing table. Ingestion never writes a fixture-specific projection. A
materialized fixture must therefore contain the complete query that can reproduce its rows from
durable catalogue evidence.

Every materialized-view fixture declares exactly one leading refresh directive:

```sql
-- atlas:refresh=keyed(tenant_id, document_id)
-- atlas:refresh=append(event_id)
-- atlas:refresh=full
```

Keyed refresh replaces only result groups identified by the ordered composite key. Append requires
that key to uniquely identify every result row and refuses source updates or deletes. Full refresh
is the explicit fallback for inherently global queries. Key columns must exist with the same names
in both the driving table and the view result so Atlas can derive them from bounded CDC changes.

A materialized-view fixture may request daily DuckLake partitioning with a single leading
`-- atlas:partition-by-day=<column>` directive. The selected `DATE` or `TIMESTAMP` result column is
partitioned by its year, month, and day transforms. Partition metadata is reconciled during setup;
changing it recreates the disposable seeded backing table rather than retaining two layouts.

Table-macro parameters may use a source-controlled SQL default in the signature. Setup preserves
the parameter order and defaults in the managed Postgres definition before recreating the DuckLake
macro. The fixture seeder derives dependencies from qualified `macros.<name>(...)` calls and
installs them in dependency order.

Before those definitions, setup reconciles every `scalar_macros/*.sql` fixture as a read-only
system definition in Postgres and deploys it into the DuckLake `macros` schema. Each file contains
one unqualified `CREATE OR REPLACE MACRO` statement whose name matches its filename. The UI exposes
these alongside user-created scalar and table macros; table macros and views may depend on them.

Fixture-owned definitions are created when missing and updated when their SQL changes. Setup fails
instead of overwriting a user-owned object with the same name and kind. Removing a scalar- or
table-macro fixture drops that fixture-owned macro on the next setup; user-owned macros are never
reconciled this way. Removed query and view fixtures remain available, including any attached
materialization.
