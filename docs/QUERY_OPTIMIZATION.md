# Query performance workflow

Use the public query API and the shared `benchmarks/query/` cases. First separate
admission/availability failures from execution cost. Classify the cause using
[QUERY.md](QUERY.md): schema/layout, optimizer behavior, or both.

Keep the user's original business query. Record the ClickHouse version, recipe,
corpus size, query, exact result comparison, elapsed time, rows/bytes read and
peak memory. Compare cold and warm runs under the same limits. Reject changes
that improve timing by returning fewer valid results or lifting resource bounds.

Keep typed bounded lookups separate from SQL transformations. One coherent
optimization belongs in one module; the query service owns execution lifecycle.
Current benchmark cases are functional baselines, not a large-scale performance
certification. Previous DuckLake/extension experiments are retained in Git history.
