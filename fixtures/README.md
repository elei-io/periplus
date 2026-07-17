# Catalogue fixtures

Atlas setup seeds every `*.sql` file in these directories through the same catalogue services
used by the UI:

- `macros/` — one `CREATE MACRO macros.<filename>(...) AS TABLE (...)` statement;
- `views/` — one `CREATE VIEW views.<filename> AS ...` statement;
- `queries/` — one read-only query, named from the filename.

Fixture-owned definitions are created when missing and updated when their SQL changes. Setup fails
instead of overwriting a user-owned object with the same name. Removing a table-macro fixture drops
that fixture-owned macro on the next setup; user-owned macros are never reconciled this way. Removed
query and view fixtures remain available, including any attached materialization.
