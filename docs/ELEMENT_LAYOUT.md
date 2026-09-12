# Canonical element layout

`material.html_elements` stores canonical element text. Links, JSON-LD and readiness
remain; parser nodes are transient preparation data only. `material.html_terms`
adds immutable term/content/node-list postings, exposed through `public_v1.html_term`.
ICU segments the parsed page once; element ranges map complete words without a
word/element interval join. No shared vocabulary allocator or DOM-specific query
rewrite is used. Public catalogue version is 2.1.0; no compatibility aliases are installed.

Full text is exact concatenation of descendant parsed text values. It includes
explicit template fragments, scripts, styles and titles, preserves whitespace,
and inserts no separators. Immediate text is stored separately. Local names keep
foreign-content case. Existing element positions retain gaps, preserving link and
JSON-LD references. Parents refer to the nearest element; root depth is zero.
Internal text offsets preserve nested-list/table exclusion in structured views.

Eight content-hash buckets and sorting by content_sha256/node_index are declared
through the materialization registry. LakeDucktor owns continuous physical merging.
The production-shaped local test must use these declarations and native merging.

Terms use eight term buckets, sorting by term/content_sha256, and 2,048-row Parquet
groups in both initial files and the table's native maintenance option. These are
the measured starting settings, not proof of a billion-capture lookup path. See
[the comparison](query-investigations/append-only-index/results.md) and
[mapping measurements](query-investigations/append-only-index/mapping.md).

Prepare/install this code as a complete generation change. Old public node/search
surfaces and obsolete internal views are removed by catalogue installation;
superseded physical materializations are removed by normal generation finalization.
Never directly delete registered files. No control schema migration is introduced.

Use explicit maintenance coordination for catalogue cutover and restart all roles
on the same immutable revision. Trigger one clean rebuild through the existing
operations API after rollout. Keep automatic image promotion paused until the
canonical generation and public queries are verified. Existing crawler pause is
preserved. Historical search experiment reports are not current contracts.
