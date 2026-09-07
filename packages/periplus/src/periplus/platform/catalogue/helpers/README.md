# Public SQL helpers

Start here to manage the helper catalogue. `HELPERS` in `__init__.py` is the explicit,
ordered registry. The public manifest includes these declarations after the evidence views.
Helpers are ordinary persistent DuckLake SQL macros, not Python UDFs or query-prep rewrites.

## Add or change a helper

1. Add one declaration module here (copy `subtree_text.py`). Declare its schema/name,
   SQL resource, parameter types/defaults, output columns/descriptions, summary,
   semantic and resource limits, executable examples, validation arguments, safe error messages, and
   required physical relations. Keep business-specific extraction out of these primitives.
2. Put the SQL in `../sql/<schema>/helpers/<name>.sql`. Use `CREATE OR REPLACE MACRO`.
   Qualify column references and choose parameter names distinct from view columns:
   DuckDB macro substitution can otherwise shadow columns inside expanded views.
3. Import the declaration into `HELPERS` in this folder's `__init__.py`.
4. Add contract tests in `tests/test_query_helpers.py` (or a focused companion file).
   Test the real public views/read-only DuckLake path as well as local fixtures.
   Test order, null/missing data, Unicode, boundaries, truncation, and lateral calls
   where relevant. Examples must bind and execute. Compare text with parser output,
   not with a second copy of the macro algorithm.
5. Update `PUBLIC_CATALOGUE_VERSION` and `docs/SCHEMA.md` when the public contract changes.
   Run `make check`, rebuild the core image, and run `periplus-setup` before rolling out
   processes that validate the new catalogue. No materialization rebuild is required
   for a helper-only change.

`GET /query/helpers` serializes public macro declarations from this registry, with the
catalogue version. It uses the query service's existing authentication. The public
Next.js proxy exposes `GET /api/query/helpers`. The agent reads it at the start of
**every request** and includes the descriptions/examples in its context. Editing a
helper needs no SDK release, duplicated TypeScript documentation, or model tool definition.
Ordinary processes validate the installed catalogue; they do not install helpers.

## Current contract: subtree_text

`content.subtree_text(source_content_id, root_element_index,
max_chars := 20000, max_elements := 10000)` returns `text`, `truncated`, `total_chars`,
and `element_count`. It reads one content-addressed subtree. Root tails are excluded;
descendant tails follow their full subtrees. Existing whitespace and code indentation
are preserved. No CSS rendering, visibility filtering, separators, trimming or semantic
cleanup is performed. This includes script/style text when those nodes are in the root.

Character truncation is explicit; element overflow fails. Missing roots return zero rows.
For an oversized page select meaningful smaller roots. Bind fixed element positions to
the content_id that was inspected; do not reuse them with a future latest observation.

The query process's memory, deadline and response limits still apply. The element limit
bounds selected structure, not total storage bytes scanned. Predicate pushdown must be
checked on representative plans before claiming a physical I/O bound.

Registry-owned `errors` are exact messages allowed through the query API. All other native DuckDB errors remain sanitized because storage errors may include credentials. Keep SQL error literals and declarations aligned; the packaging/registry tests verify them.
