import assert from "node:assert/strict";
import test from "node:test";
import { AtlasConsole } from "./console.js";
import type { AtlasApi, GraphRun, GraphRunTrigger } from "./types.js";

function graphRun(overrides: Partial<GraphRun> = {}): GraphRun {
  return {
    id: "run-1",
    graph_id: "graph-1",
    graph_slug: "single-page",
    status: "running",
    trigger_kind: "manual",
    trigger_schedule_id: null,
    trigger_urls: ["https://example.com/"],
    max_crawls: 100,
    crawl_limit_reached: false,
    request_count: 1,
    pending_request_count: 1,
    failed_request_count: 0,
    error_count: 0,
    created_at: "2026-07-24T00:00:00Z",
    started_at: "2026-07-24T00:00:01Z",
    last_progress_at: "2026-07-24T00:00:02Z",
    completed_at: null,
    paused_at: null,
    cancel_requested_at: null,
    error: null,
    ...overrides,
  };
}

function api(): AtlasApi {
  return {
    graphs: {
      async list() {
        return {
          total: 2,
          items: [
            {
              id: "graph-1",
              slug: "single-page",
              description: "Capture one page",
              root_node_id: "node-1",
              system_owned: true,
              created_at: "2026-07-24T00:00:00Z",
            },
            {
              id: "graph-2",
              slug: "scratch",
              description: null,
              root_node_id: null,
              system_owned: false,
              created_at: "2026-07-24T00:00:00Z",
            },
          ],
        };
      },
      async run(graphId: string, input: GraphRunTrigger) {
        assert.equal(graphId, "graph-1");
        assert.deepEqual(input, {
          urls: ["https://example.com/", "https://example.org/"],
          max_crawls: 25,
        });
        return { graph_id: graphId, run_id: "run-1", status: "queued" };
      },
    },
    runs: {
      async list() {
        return { items: [graphRun()], total: 1 };
      },
      async get(runId: string) {
        assert.equal(runId, "run-1");
        return graphRun();
      },
      async failures(runId: string) {
        assert.equal(runId, "run-1");
        return {
          total: 1,
          items: [{
            failure_stage: "acquisition",
            failure_code: "timeout",
            status_code: null,
            count: 1,
            example_url: "https://example.com/",
            example_detail: "navigation timed out",
            last_occurred_at: "2026-07-24T00:00:03Z",
          }],
        };
      },
      async pause() {
        return graphRun({ status: "paused" });
      },
      async resume() {
        return graphRun({ status: "running" });
      },
      async cancel() {
        return graphRun({ status: "cancelled" });
      },
      async *follow(runId: string) {
        assert.equal(runId, "run-1");
        yield {
          kind: "progress",
          nodes: {
            node: {
              admitted: 1,
              queued: 0,
              crawling: 1,
              awaiting_navigation: 0,
              evaluating_edges: 0,
              completed: 0,
              failed: 0,
              cancelled: 0,
              settled: false,
            },
          },
          edges: {},
        } as const;
        yield {
          kind: "settled",
          run: graphRun({
            status: "completed",
            pending_request_count: 0,
            completed_at: "2026-07-24T00:00:04Z",
          }),
        } as const;
      },
    },
    catalogue: {
      async execute(sql: string) {
        assert.equal(sql, "select * from elements limit 10;");
        return {
          statementKind: "query",
          columns: ["tag_name", "count"],
          columnTypes: ["Utf8", "Int64"],
          rows: [["a", 42]],
        };
      },
      async metadata() {
        return {
          catalog_name: "atlas",
          default_schema: "main",
          relations: [
            {
              catalog_name: "atlas",
              schema_name: "main",
              name: "elements",
              kind: "table",
              columns: [
                {
                  name: "tag_name",
                  data_type: "VARCHAR",
                  nullable: false,
                },
                {
                  name: "crawl_id",
                  data_type: "UUID",
                  nullable: false,
                },
              ],
            },
            {
              catalog_name: "atlas",
              schema_name: "views",
              name: "page_links",
              kind: "view",
              columns: [],
            },
          ],
          functions: [
            {
              catalog_name: "atlas",
              schema_name: "main",
              name: "count_if",
              kind: "scalar",
              description: "Count matching rows",
              return_type: "BIGINT",
              parameters: [
                { name: "condition", data_type: "BOOLEAN" },
              ],
              varargs: null,
              result_columns: [],
            },
          ],
        };
      },
      async runtime() {
        return {
          transport: "api",
          mutation_policy: "read_only",
          result_format: "arrow_ipc_stream",
          cancellation_supported: true,
          maximum_concurrency: 4,
          query_timeout_seconds: 30,
          maximum_rows: 10_000,
          maximum_result_bytes: 16_777_216,
        };
      },
      async status() {
        return {
          lake_slug: "atlas_test",
          active_file_count: 12,
          active_storage_bytes: 1_048_576,
          ducklake_version: "1.3",
          catalogue_schema_version: "4",
          compiler_version: "0.1.0",
        };
      },
    },
  } as unknown as AtlasApi;
}

test(".help is generated from the command registry", async () => {
  const result = await new AtlasConsole(api()).execute(".help");
  assert.equal(result?.kind, "table");
  if (result?.kind !== "table") return;
  assert.deepEqual(result.columns, ["Command", "Description"]);
  for (const expected of [
    [".help [resource]", "Show available Atlas commands"],
    [".clear", "Clear the console"],
    [".status", "Show catalogue runtime and limits"],
    [".describe <relation>", "Describe a catalogue relation"],
    [".completion reload", "Reload catalogue completion metadata"],
    [".graphs list", "List crawl graphs"],
    [".graphs show <slug>", "Open a crawl graph in Atlas Web"],
    [
      ".graphs run <slug> [--url <value> ...] [--max-crawls <value>]",
      "Start a crawl graph run",
    ],
    [".runs list", "List graph runs"],
    [".runs show <run>", "Show a graph run"],
    [".runs follow <run>", "Follow graph run progress until it settles"],
    [".runs failures <run>", "Show grouped graph run failures"],
    [".runs pause <run>", "Pause graph run admission"],
    [".runs resume <run>", "Resume a paused graph run"],
    [".runs cancel <run>", "Cancel a graph run"],
    [".runs open <run>", "Open a graph run in Atlas Web"],
  ]) {
    assert.ok(
      result.rows.some((row) => row[0] === expected[0] && row[1] === expected[1]),
      `${expected[0]} was missing from .help`,
    );
  }
});

test(".clear returns a platform-neutral clear action", async () => {
  const result = await new AtlasConsole(api()).execute(".clear");
  assert.deepEqual(result, { kind: "clear" });
});

test(".graphs list maps API records to a structured table", async () => {
  const result = await new AtlasConsole(api()).execute(".graphs list");
  assert.deepEqual(result, {
    kind: "table",
    columns: ["Slug", "Description", "Root", "Ownership"],
    rows: [
      ["single-page", "Capture one page", "configured", "system"],
      ["scratch", "", "missing", "user"],
    ],
  });
});

test(".graphs show resolves a slug to a web navigation result", async () => {
  const result = await new AtlasConsole(api()).execute(
    ".graphs show single-page",
  );
  assert.deepEqual(result, {
    kind: "navigate",
    path: "/crawls/graphs/graph-1",
    label: "Open single-page in Atlas Web",
  });
});

test(".graphs show rejects an unknown slug", async () => {
  await assert.rejects(
    new AtlasConsole(api()).execute(".graphs show absent"),
    /Crawl graph not found: absent/,
  );
});

test(".graphs run validates options, starts a run, and selects it for the session", async () => {
  const console = new AtlasConsole(api());
  const started = await console.execute(
    ".graphs run single-page --url https://example.com/ " +
      "--url https://example.org/ --max-crawls 25",
  );
  assert.deepEqual(started, {
    kind: "message",
    text:
      "Started single-page run run-1.\n" +
      "Follow with .runs follow run-1",
  });

  const opened = await console.execute(".runs open last");
  assert.deepEqual(opened, {
    kind: "navigate",
    path: "/crawls/graphs/graph-1",
    label: "Open run run-1 in Atlas Web",
  });
});

test(".graphs run requires safe root URLs", async () => {
  await assert.rejects(
    new AtlasConsole(api()).execute(".graphs run single-page"),
    /At least one --url is required/,
  );
  await assert.rejects(
    new AtlasConsole(api()).execute(
      ".graphs run single-page --url file:\/\/\/tmp\/page.html",
    ),
    /Crawl URL must be absolute HTTP\(S\)/,
  );
});

test(".runs commands expose run state, failures, and controls", async () => {
  const console = new AtlasConsole(api());

  const listed = await console.execute(".runs list");
  assert.equal(listed?.kind, "table");
  if (listed?.kind === "table") {
    assert.equal(listed.summary, "1 run");
    assert.equal(listed.rows[0]?.[0], "run-1");
  }

  const shown = await console.execute(".runs show run-1");
  assert.equal(shown?.kind, "table");
  if (shown?.kind === "table") {
    assert.ok(
      shown.rows.some((row) => row[0] === "Status" && row[1] === "running"),
    );
  }

  const failures = await console.execute(".runs failures last");
  assert.equal(failures?.kind, "table");
  if (failures?.kind === "table") {
    assert.equal(failures.summary, "1 failed request");
    assert.equal(failures.rows[0]?.[1], "timeout");
  }

  assert.deepEqual(await console.execute(".runs pause last"), {
    kind: "message",
    text: "Run run-1 is paused.",
  });
  assert.deepEqual(await console.execute(".runs resume last"), {
    kind: "message",
    text: "Run run-1 is running.",
  });
  assert.deepEqual(await console.execute(".runs cancel last"), {
    kind: "message",
    text: "Run run-1 is cancelled.",
  });
});

test(".runs follow streams progress and the settled run", async () => {
  const console = new AtlasConsole(api());
  const result = await console.execute(".runs follow run-1");
  assert.equal(result?.kind, "stream");
  if (result?.kind !== "stream") return;

  const events = [];
  for await (const event of result.events) events.push(event);
  assert.equal(events.length, 3);
  assert.deepEqual(events[0], {
    kind: "message",
    text: "Following run run-1…",
  });
  assert.equal(events[1]?.kind, "table");
  if (events[1]?.kind === "table") {
    assert.deepEqual(events[1].rows, [[1, 0, 1, 0, 0, 0, "0/0", 0]]);
  }
  assert.equal(events[2]?.kind, "table");
  if (events[2]?.kind === "table") {
    assert.ok(
      events[2].rows.some(
        (row) => row[0] === "Status" && row[1] === "completed",
      ),
    );
  }
});

test("non-command input executes as catalogue SQL", async () => {
  const result = await new AtlasConsole(api()).execute(
    "select * from elements limit 10;",
  );
  assert.equal(result?.kind, "table");
  if (result?.kind !== "table") return;
  assert.deepEqual({
    kind: result.kind,
    columns: result.columns,
    rows: result.rows,
  }, {
    kind: "table",
    columns: ["tag_name", "count"],
    rows: [["a", 42]],
  });
  assert.match(
    result.summary ?? "",
    /^1 row · \d+(?:ms|s)$/,
  );
});

test(".history exposes the shared editor history", async () => {
  const console = new AtlasConsole(api());
  console.history.push("select 1;", ".graphs list");
  assert.deepEqual(await console.execute(".history"), {
    kind: "table",
    columns: ["#", "Input"],
    rows: [[1, "select 1;"], [2, ".graphs list"]],
  });
});

test(".describe resolves metadata and returns typed columns", async () => {
  assert.deepEqual(
    await new AtlasConsole(api()).execute(".describe elements"),
    {
      kind: "table",
      columns: ["Column", "Type", "Nullable"],
      rows: [
        ["tag_name", "VARCHAR", "no"],
        ["crawl_id", "UUID", "no"],
      ],
      summary: "main.elements · table",
    },
  );
});

test(".status combines runtime limits and catalogue state", async () => {
  const result = await new AtlasConsole(api()).execute(".status");
  assert.equal(result?.kind, "table");
  if (result?.kind !== "table") return;
  assert.deepEqual(result.rows.slice(0, 5), [
    ["Lake", "atlas_test"],
    ["Transport", "api"],
    ["Mutation policy", "read_only"],
    ["Result format", "arrow_ipc_stream"],
    ["Cancellation", "supported"],
  ]);
});

test(".completion reload reports the refreshed metadata index", async () => {
  assert.deepEqual(
    await new AtlasConsole(api()).execute(".completion reload"),
    {
      kind: "message",
      text: "Completion metadata reloaded: 2 relations, 2 columns, 1 functions.",
    },
  );
});

test(".ai keeps bounded context and shows or runs numbered SQL suggestions", async () => {
  const service = api();
  const contexts: unknown[][] = [];
  const executed: string[] = [];
  service.catalogue.execute = async (sql) => {
    executed.push(sql);
    return {
      statementKind: "query",
      columns: ["value"],
      columnTypes: ["INTEGER"],
      rows: [[1]],
    };
  };
  service.ai = {
    async *ask(prompt, context) {
      contexts.push([...context]);
      yield {
        type: "tool.started",
        run_id: "ai-1",
        tool: "inspect catalogue",
      };
      yield {
        type: "tool.completed",
        run_id: "ai-1",
        tool: "inspect catalogue",
        duration_ms: 12,
      };
      yield {
        type: "response.completed",
        run_id: "ai-1",
        response: {
          kind: "message",
          message: `Query for ${prompt}`,
        },
        suggestions: [
          {
            title: "Elements sample",
            description: "Inspect retained elements.",
            authored_sql: "select * from elements limit 10;",
            executable_sql: "SELECT * FROM elements LIMIT 10",
          },
          {
            title: "Document count",
            description: "Count retained documents.",
            authored_sql: "select count(*) from documents limit 1;",
            executable_sql: "SELECT count(*) FROM documents LIMIT 1",
          },
        ],
      };
    },
  };
  const atlas = new AtlasConsole(service);

  const first = await atlas.execute('.ai "Book prices?"');
  assert.equal(first?.kind, "stream");
  if (first?.kind === "stream") {
    const events = [];
    for await (const event of first.events) events.push(event);
    assert.equal(events.length, 5);
    assert.deepEqual(events.slice(1, 4), [
      {
        kind: "progress",
        state: "active",
        activity: "catalogue",
        label: "Inspect catalogue",
      },
      {
        kind: "progress",
        state: "completed",
        activity: "catalogue",
        label: "Inspect catalogue",
        durationMilliseconds: 12,
      },
      {
        kind: "progress",
        state: "active",
        activity: "thinking",
        label: "Working",
      },
    ]);
  }
  const second = await atlas.execute('.ai "And by category?" --context 1');
  if (second?.kind === "stream") {
    for await (const _event of second.events) {
      // Drain the response so it becomes session context.
    }
  }
  const shown = await atlas.execute(".ai show 2");
  const query = await atlas.execute(".ai run 2");

  assert.deepEqual(contexts[0], []);
  assert.deepEqual(contexts[1], [
    {
      role: "assistant",
      content:
        "Query for Book prices?\nElements sample: Inspect retained elements.\nDocument count: Count retained documents.",
    },
  ]);
  assert.deepEqual(shown, {
    kind: "assistant",
    state: "completed",
    text: "Document count\nCount retained documents.",
    sql: "select count(*) from documents limit 1;",
    sqlRunCommand: ".ai run 2",
  });
  assert.equal(query?.kind, "table");
  assert.deepEqual(executed, ["select count(*) from documents limit 1;"]);
  await assert.rejects(
    atlas.execute(".ai run"),
    /Choose a suggestion from 1 to 2/,
  );
});

test("interrupt aborts the active catalogue query", async () => {
  const fake = api();
  let observedAbort = false;
  fake.catalogue.execute = async (_sql, signal) =>
    new Promise((_resolve, reject) => {
      signal?.addEventListener("abort", () => {
        observedAbort = true;
        reject(signal.reason);
      }, { once: true });
    });
  const console = new AtlasConsole(fake);
  const pending = console.execute("select * from slow_relation;");
  await Promise.resolve();
  assert.equal(console.interrupt(), true);
  await assert.rejects(pending, (reason: unknown) =>
    reason instanceof Error && reason.name === "AbortError",
  );
  assert.equal(observedAbort, true);
  assert.equal(console.interrupt(), false);
});

test("command completion covers resources, actions, and API-backed arguments", async () => {
  const console = new AtlasConsole(api());
  assert.deepEqual(
    (await console.complete(".gra")).map((item) => item.insertText),
    [".graphs"],
  );
  assert.deepEqual(
    (await console.complete(".graphs sh")).map((item) => item.insertText),
    ["show"],
  );
  assert.deepEqual(
    (await console.complete(".graphs show sin")).map(
      (item) => item.insertText,
    ),
    ["single-page"],
  );
  assert.deepEqual(
    (await console.complete(".help gr")).map((item) => item.insertText),
    ["graphs"],
  );
});

test("SQL completion covers keywords, relations, functions, and aliased columns", async () => {
  const console = new AtlasConsole(api());
  assert.deepEqual(
    (await console.complete("sel")).map((item) => item.insertText),
    ["SELECT"],
  );
  assert.deepEqual(
    (await console.complete("select * from ele")).map(
      (item) => item.insertText,
    ),
    ["elements"],
  );
  assert.deepEqual(
    (await console.complete("select count_")).map(
      (item) => item.insertText,
    ),
    ["count_if("],
  );
  assert.deepEqual(
    (
      await console.complete(
        "select e.tag from elements e",
        "select e.tag".length,
      )
    ).map((item) => item.insertText),
    ["e.tag_name"],
  );
  assert.deepEqual(
    (await console.complete("select * from views.page")).map(
      (item) => item.insertText,
    ),
    ["views.page_links"],
  );
});
