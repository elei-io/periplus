# Developing the query API

Both endpoints expose the same six public primitives and `search(terms)`. Stable
uses native DuckDB execution. Experimental adds bounded exact-text and selected-capture link
passes. Neither endpoint owns a separate compiler or execution engine.

## Where to work

| File | Owns |
| --- | --- |
| `models.py` | Request/result types and compiler version |
| `validation.py` | Public SQL syntax and namespace admission |
| `compiler.py` | Native binding, pass selection, plans and diagnostics |
| `optimizations/__init__.py` | Explicit ordered stable and experimental pass lists |
| `optimizations/base.py` | Pass input and typed decision |
| `optimizations/element_text.py` | The exact-text candidate algorithm |
| `optimizations/capture_links.py` | Selected capture lookup and scoped link scan |
| `optimizations/_catalogue.py` | Shared installed-view AST comparison |
| `service.py` | One connection, admission, snapshot, deadline, result limits and cleanup |
| `benchmarking.py` | Same-snapshot result and performance comparison; never request handling |

Catalogue view/macro SQL belongs in `platform/catalogue/sql/`, not in a compiler
pass. Change the schema when its semantics are wrong. Use a pass only to preserve
those semantics while improving physical execution. See
[the query contract](../../../../../docs/QUERY.md) and
[performance triage](../../../../../docs/QUERY_OPTIMIZATION.md).

## The execution path

The service admits one request, starts its deadline and read transaction, and pins
the source snapshot. The compiler validates public access and binds the submitted
SQL before allowing a pass to read anything. Invalid public SQL cannot use a pass
to access private tables. The selected executable and its plan return to the
service for bounded result delivery and transaction cleanup.

Passes are ordered **alternatives**, not a rewrite chain. Each gets a copy of the
original AST. Declines continue to the next pass; the first applied pass wins.
For preparation, the first deferred pass stops selection. Inspection statements
(`EXPLAIN`, `DESCRIBE`, `SUMMARIZE`, `SHOW`) use native behavior and run no passes.

A pass must not mutate parameters, commit, open connections, install objects,
change settings or start its own deadline. Database reads use only the provided
connection inside the existing snapshot and deadline. Preparation provides no
connection: matching can inspect syntax, but data-dependent decisions defer.
Unexpected failures propagate through the normal cleanup path; they are never
silently converted into a native retry.

## Adding, disabling and removing a pass

1. Add one module under `optimizations/`. Export an `OptimizationPass(name, run)`.
   Keep syntax matching, contract checks, bounded lookup and rewriting in named
   functions in that module. Document the no-false-negative argument and link to
   measured physical evidence beside non-obvious SQL choices.
2. Register it in `EXPERIMENTAL_PASSES`. Order is explicit. No service edit, dynamic
   discovery, subclass or special HTTP/CLI branch is needed.
3. Add real DuckDB differential tests for columns, types, rows, multiplicities and
   ordering. Test rejected shapes, budget overflow, catalogue mismatch, no matches
   and preparation without database reads. Add service integration tests when the
   pass introduces a new kind of execution work.
4. Add a representative case under `benchmarks/query/cases/` and compare the pass
   enabled and disabled in the same snapshot, in both run orders.
5. Promote only after the evidence meets the acceptance rules. Move the pass from
   `EXPERIMENTAL_PASSES` to `STABLE_PASSES`; experimental includes the stable list.
   Matching uses `context.schema`, so test both qualified schemas before promotion.

To disable a pass, remove its registration. To delete it, also remove its module
and dedicated tests, and update or retire its benchmark cases and documentation.
Private callers/tests may construct `QueryService(..., passes=())` for a native
baseline. Pass overrides are not HTTP request fields.

## Understanding a decision

`PassDecision` has a status, stable reason code, safe explanation, optional counts,
and a rewritten AST only when applied:

| Status | Meaning |
| --- | --- |
| `not_applicable` | The query shape is unsupported or already has a direct path |
| `deferred` | Syntax matches; execution must perform a bounded lookup |
| `contract_mismatch` | Installed semantics or physical assumptions differ |
| `budget_exceeded` | A candidate limit was exceeded; use native SQL without truncating matches |
| `applied` | Execute the rewritten query and keep the original result semantics |

Responses preserve submitted SQL and parameters. `optimizations` lists applied
pass names; diagnostics use `<pass>.<status>.<reason>` and optional bounded counts.
Use fixed messages and counts only: never include query literals, URLs, content
IDs, row IDs, native errors or credentials in diagnostic messages. These messages
also enter private query history. No new telemetry or persistence path is added.

The exact-text pass deliberately declines single words such as `text = 'robot'`,
parameters, joins and collations. Its safe interior-word anchor is narrower than
search semantics. `search(['robot'])` is a public catalogue macro, not a compiler
pass; it works in both modes independently of pass registration.

The capture-link pass recognizes one deterministic, limited capture CTE for literal
page URLs and one inner capture-ID join to links. Its named functions match syntax,
check the installed contract, fetch bounded keys and rewrite the scan. It resolves
at most 128 captures (8 KiB per source URL, 128 KiB total keys), then reuses the IDs
in the CTE, retaining duplicate IDs. Literal visit and normalized source-URL filters
use the existing physical sort key. Redirects use effective URL, falling back to
page URL. Unsupported shapes and over-budget selections keep native execution.
See [the investigation](../../../../../docs/query-investigations/single-capture-links/README.md).

## Running checks and comparisons

From `packages/periplus/`, with the local control-database environment configured:

```sh
uv run python -m unittest discover -s tests -p 'test_query_compiler.py'
uv run python -m unittest discover -s tests -p 'test_text_index.py'
uv run python -m unittest discover -s tests -p 'test_query_service.py'
uv run python scripts/query_benchmark.py --case index-text-equality \
  --optimization element_text_index_candidates --warm-runs 1 \
  --ordinary-warm-runs --report ../../.artifacts/query-benchmarks/pass.json
```

Use an existing eligible case from `benchmarks/query/cases/`; benchmark SQL uses
`public_v1` or `experimental` against the portable contract. The runner infers the
pass namespace from qualified case tables and rejects mixed schemas. `--optimization` selects a registered
pass directly for a controlled comparison, regardless of its production mode.
Candidate lookup is included in measured time. The report records decision status,
reason, counts and `lookup_and_rewrite_ms` even when the pass declines. Repeat with `--candidate-first` to
check run-order bias. Do not compare rewritten SQL alone and omit its lookup cost.
Production acceptance must additionally exercise the unchanged user SQL through
the actual service. Run `make check` from the repository root before pushing.
