# Atlas console

The Atlas console is one command system with platform adapters. `console-core`
contains command declarations, parsing, validation, completion, execution,
structured results, SQL completion, and the shared line editor. The Node CLI
adapts stdin/stdout and browser opening. Atlas Web adapts wterm input/output,
browser-local history, and in-app navigation to the same core.

Interactive Atlas commands start with a dot:

```text
.help
.graphs list
.graphs show single-page
```

Input without a dot is catalogue SQL:

```sql
select * from elements limit 10;
```

SQL continues across lines until it ends with a semicolon:

```text
atlas> select
  ...>   count(*)
  ...> from crawls;
```

Interactive queries display elapsed progress after 150 ms. Ctrl-C aborts the
local Arrow request and, once Atlas has returned the query identity, sends the
authoritative cancellation request to the API. Successful results include a
row-count and duration summary.

Headless arguments are classified through the same registry:

```text
atlas graphs list
atlas "select count(*) from crawls;"
```

## Adding a command

Declare a command beside its resource and register it once in
`commands/index.ts`. `defineCommand` derives canonical usage from its declared
arguments and options. Every command must have an example.

### No-input command

```ts
export const listWidgets = defineCommand({
  path: ["widgets", "list"],
  summary: "List widgets",
  examples: [".widgets list"],
  async execute(_invocation, context) {
    const widgets = await context.api.widgets.list(context.signal)
    return {
      kind: "table",
      columns: ["Name", "State"],
      rows: widgets.map((widget) => [widget.name, widget.state]),
    }
  },
})
```

The path automatically supplies `.widgets` and `list` completion.

### Positional argument with fixed choices

Use `argument.choice` when the valid vocabulary belongs to the command:

```ts
defineCommand({
  path: ["widgets", "inspect"],
  summary: "Inspect widgets at one detail level",
  arguments: [
    argument.string("widget"),
    argument.choice("detail", ["summary", "full"]),
  ],
  examples: [".widgets inspect primary full"],
  execute({ positionals }) {
    return inspect(positionals.widget!, positionals.detail!)
  },
})
```

Choices drive completion and validation. Commands must not reimplement either.

### API-backed resource argument

Use `resourceCompletion` for server-owned identities:

```ts
argument.string("slug", {
  description: "Crawl graph slug",
  complete: resourceCompletion({
    load: async ({ context }) =>
      (await context.api.graphs.list(context.signal)).items,
    value: (graph) => graph.slug,
    description: (graph) => graph.description ?? undefined,
    kind: "argument",
  }),
})
```

The provider receives the current `AbortSignal`. Return domain objects from
`load`; the helper owns short-lived request caching, prefix filtering, and
conversion to completion items. The default cache is five seconds. Set
`cacheMilliseconds: 0` only for values that must be fetched on every request.

### Options

Declare boolean, string, integer, repeatable string, and choice options:

```ts
options: [
  option.boolean("follow", {
    description: "Follow the operation until it settles",
  }),
  option.string("label"),
  option.integer("limit", { minimum: 1, maximum: 10_000 }),
  option.strings("url", {
    description: "Root URL; may be repeated",
  }),
  option.choice("format", ["table", "json"]),
]
```

The registry completes option names, excludes non-repeatable options already
used, completes choice values, validates integer bounds, rejects unknown or
duplicate non-repeatable options, and returns parsed values:

```ts
execute({ positionals, options }) {
  const follow = options.follow === true
  const limit = options.limit
  const urls = options.url ?? []
  const format = options.format ?? "table"
}
```

Use a custom `complete` provider on an argument or string option only when its
values cannot be expressed as choices or a resource list. Repeatable options
remain available to completion after their first use.

## Completion contract

Every provider returns `CompletionItem` values:

```ts
{
  insertText: "single-page",
  replaceStart: 13,
  replaceEnd: 16,
  kind: "argument",
  description: "Acquire exactly one page",
  priority: 50,
}
```

The replacement range is mandatory. Providers do not edit terminal state.
`safeCompletions` removes control characters and duplicates, then applies
deterministic priority and alphabetical ordering.

Completion sources are intentionally layered:

1. The command registry derives resources, actions, options, and fixed choices.
2. Resource providers supply API-backed argument values.
3. `SqlCompleter` supplies catalogue relations, schemas, functions, aliased
   columns, and SQL keywords.
4. `GhostTextEditor` displays and accepts the ranked result.

The shared editor debounces requests, discards stale asynchronous results,
shows dimmed ghost text, accepts it with Tab or right arrow, cycles with
Shift-Tab, and dismisses it with Escape. Platform adapters implement only
`InteractiveTerminal.writeRaw` and `InteractiveTerminal.onData`.

## SQL completion

SQL completion is deterministic and metadata-backed:

```text
sel
  → SELECT

select * from ele
              → elements

select count_
       → count_if(

select e.tag from elements e
         → e.tag_name

select * from elements e join crawls c on e.crawl_id = c.cra
                                                        → c.crawl_id

with recent as (
  select crawl_id as id from crawls
)
select recent.i from recent
              → recent.id

select "e"."Di" from "main"."elements" as "e"
            → "e"."Display Name"

select * from page_
              → page_links(crawl UUID) → target_url VARCHAR, …
```

Catalogue metadata is shared by completion requests and refreshed after thirty
seconds so definitions changed elsewhere become visible without restarting the
console. `.completion reload` invalidates it immediately; future commands that
mutate catalogue definitions must call the same invalidation boundary after
success. SQL completion does not run SQL, does not use an agent, and returns
nothing inside strings, comments, or after a terminating semicolon.

The analyzer tracks `FROM`, comma joins, every `JOIN`, CTEs, derived tables,
outer aliases visible to correlated subqueries, table-macro result columns, and
the active function argument. Unqualified columns are offered in `SELECT`,
`WHERE`, `ON`, `GROUP BY`, `ORDER BY`, and `HAVING`; ambiguous names are
qualified automatically. Completion descriptions show the owning alias,
column type and nullability, or the complete function signature and expected
parameter.

Keep the layers separate:

- `sql-completion.ts` understands tolerant SQL context.
- `sql-analysis.ts` tokenizes incomplete SQL and derives scopes, CTEs,
  relations, aliases, nested queries, and active function calls.
- API adapters load catalogue metadata.
- Commands never contain SQL completion rules.
- Editors never contain Atlas or SQL semantics.

## Results and platform actions

Commands return structured results rather than writing output:

- `table` for tabular data;
- `message` for a concise outcome;
- `navigate` to open an Atlas Web resource;
- `clear` to clear the current console.
- `stream` for a sequence of the other result types.

The Node adapter renders tables, opens `navigate` results with the operating
system browser, and emits the clear-screen sequence. The web adapter renders
terminal-native tables into wterm, routes `navigate` in-app, and clears wterm
without changing a command. Result layout belongs to each adapter; the core
never returns ANSI or HTML.

SQL-shell convenience commands are ordinary shared commands:

```text
.status                 catalogue state, transport, limits, and cancellation
.describe <relation>    columns, types, nullability, and relation kind
.history                inputs retained for the current console session
.completion reload      refresh schemas, relations, columns, and functions
```

Atlas AI is also an ordinary shared command:

```text
.ai "How are book prices distributed?" [--context 6]
.ai 2
.ai 2 --copy
.ai 2 --show
```

The first command sends at most the selected number of recent user/assistant
messages, held only by the current `AtlasConsole` instance. `--context 0`
starts a context-free turn. Tool completions stream as timed progress messages.
The terminal response is concise prose followed by at most three numbered SQL
suggestion titles and descriptions; it does not inline the SQL. `.ai N`
executes its authored SQL through the normal interactive catalogue boundary,
`.ai N --copy` copies it, and `.ai N --show` reveals it without execution.
A suggestion is registered only after the
read-only validator and compiler accept it without errors or warnings. The agent
never executes that handoff implicitly. Atlas API, Postgres, and NATS do not
persist prompts, responses, suggestions, or chat context. Browser-local command
history does not include AI message context.

The editor and `.history` share the same session array. The Node adapter leaves
it process-local; the web adapter hydrates and persists a bounded copy in
browser local storage. Catalogue completion metadata remains session-local and
is refreshed explicitly after catalogue definitions change.

## Graph-run workflow

Graph runs are the first complete operational workflow:

```text
.graphs list
.graphs show <slug>
.graphs run <slug> --url <url> [--url <url> ...] [--max-crawls N]
.graphs run <slug> --from-result <column> [--max-crawls N]
.graphs confirm

.runs list
.runs show <id|last>
.runs follow <id|last>
.runs failures <id|last>
.runs pause <id|last>
.runs resume <id|last>
.runs cancel <id|last>
.runs open <id|last>
```

Every graph submission is first held in the console session and displayed with
its distinct URL count, rejected and duplicate counts, and a five-URL sample.
`.graphs confirm` submits exactly that frozen URL list.

`--from-result` refers to the latest successfully executed SQL statement, not
its displayed rows. Atlas re-executes the statement through the catalogue query
boundary, reads the selected column, and then applies the 10,000-root graph-run
limit. This avoids the AI investigation tool's 200-row display bound. Both an
ordinary SQL statement and `.ai N` replace the session's latest SQL
statement.

Starting or inspecting a run selects it for the current session. Subsequent
commands may use `last`, avoiding run-ID copying through the common
start/follow/failures loop:

```text
atlas> .graphs run single-page --url https://example.com --max-crawls 1
Ready to start single-page with 1 distinct root URL.
Confirm with .graphs confirm

atlas> .graphs confirm
Started single-page run 0198….
Follow with .runs follow 0198…

atlas> .runs follow last
Following run 0198…
```

`.runs follow` consumes server-sent progress snapshots and yields structured
table events until the terminal run record arrives. Interactive terminals redraw
each progress table in place; JSON and non-interactive consumers retain every
snapshot. Ctrl-C aborts the event request without cancelling the graph run;
`.runs cancel last` is the explicit durable cancellation operation. `.graphs show`
and `.runs open` return
navigation actions, so the Node adapter opens Atlas Web and the web adapter
routes in place.

Terminal values use unambiguous representations:

- SQL null is `NULL`, distinct from an empty string;
- timestamps are ISO-8601 strings;
- arrays and nested values use compact JSON;
- binary values use hexadecimal;
- big integers are rendered without loss;
- wide values are deterministically truncated to the terminal width.

## Required tests

For each command, cover:

1. canonical usage;
2. successful typed API mapping;
3. argument and option validation;
4. structured output;
5. every custom completion provider;
6. missing-resource and API errors.

Registry-wide tests cover derived path, choice, and option completion. Editor
tests cover display and acceptance independently from command tests. SQL
completion tests cover keywords, relations, functions, aliases, and contexts
where completion must remain silent.
