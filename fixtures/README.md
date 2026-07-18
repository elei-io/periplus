# Catalogue fixtures

Atlas setup seeds every source-controlled `*.sql` fixture. User-visible definitions in these
directories go through the same catalogue services used by the UI:

- `table_macros/` — one `CREATE MACRO macros.<filename>(...) AS TABLE (...)` statement;
- `views/` — one `CREATE VIEW views.<filename> AS ...` statement;
- `queries/` — one read-only query, named from the filename.

Before those definitions, setup reconciles every `scalar_macros/*.sql` fixture as a read-only
system definition in Postgres and deploys it into the DuckLake `macros` schema. Each file contains
one unqualified `CREATE OR REPLACE MACRO` statement whose name matches its filename. The UI exposes
these alongside user-created scalar and table macros; table macros and views may depend on them.

Fixture-owned definitions are created when missing and updated when their SQL changes. Setup fails
instead of overwriting a user-owned object with the same name and kind. Removing a scalar- or
table-macro fixture drops that fixture-owned macro on the next setup; user-owned macros are never
reconciled this way. Removed
query and view fixtures remain available, including any attached materialization.
