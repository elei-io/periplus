# Canonical element layout

This release replaces persisted nodes and all text-search materializations with
`material.html_elements`. Links, JSON-LD and readiness remain. Parser nodes are
transient preparation data only. There is no vocabulary, term/posting relation,
search API, subtree_text helper, ICU tokenizer, or DOM-specific query rewrite.
Public schema version is 2.0.0; no compatibility aliases are installed.

Full text is exact concatenation of descendant parsed text values. It includes
explicit template fragments, scripts, styles and titles, preserves whitespace,
and inserts no separators. Immediate text is stored separately. Local names keep
foreign-content case. Existing element positions retain gaps, preserving link and
JSON-LD references. Parents refer to the nearest element; root depth is zero.
Internal text offsets preserve nested-list/table exclusion in structured views.

Eight content-hash buckets and sorting by content_sha256/node_index are declared
through the materialization registry. LakeDucktor owns continuous physical merging.
The production-shaped local test must use these declarations and native merging.

Prepare/install this code as a complete generation change. Old public node/search
surfaces and obsolete internal views are removed by catalogue installation;
superseded physical materializations are removed by normal generation finalization.
Never directly delete registered files. No control schema migration is introduced.

Use explicit maintenance coordination for catalogue cutover and restart all roles
on the same immutable revision. Trigger one clean rebuild through the existing
operations API after rollout. Keep automatic image promotion paused until the
canonical generation and public queries are verified. Existing crawler pause is
preserved. Historical search experiment reports are not current contracts.
