# Catalogue fixtures

Atlas setup seeds every `*.sql` file in these directories through the same catalogue services
used by the UI:

- `macros/` — one `CREATE MACRO macros.<filename>(...) AS TABLE (...)` statement;
- `views/` — one `CREATE VIEW views.<filename> AS ...` statement;
- `queries/` — one read-only query, named from the filename.

Fixture-owned definitions are created when missing and updated when their SQL changes. Setup fails
instead of overwriting a user-owned object with the same name. Removing a fixture does not delete
an existing definition or any attached materialization.
