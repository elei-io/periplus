import { argument, defineCommand } from "../command.js";
import { ConsoleError } from "../errors.js";
import type {
  AtomicCommandResult,
  CommandContext,
  CompletionProvider,
  GraphRun,
  GraphRunEvent,
} from "../types.js";

const runArgument = () =>
  argument.string("run", {
    description: "Graph run ID or 'last'",
    complete: runCompletion(),
  });

export const listRunsCommand = defineCommand({
  path: ["runs", "list"],
  summary: "List graph runs",
  examples: [".runs list"],
  async execute(_invocation, context) {
    const runs = await context.api.runs.list(context.signal);
    return {
      kind: "table",
      columns: [
        "Run",
        "Graph",
        "Status",
        "Requests",
        "Pending",
        "Failed",
        "Created",
      ],
      rows: runs.items.map((run) => [
        run.id,
        run.graph_slug ?? run.graph_id,
        run.status,
        run.request_count,
        run.pending_request_count,
        run.failed_request_count,
        formatTimestamp(run.created_at),
      ]),
      summary: `${runs.total} ${runs.total === 1 ? "run" : "runs"}`,
    };
  },
});

export const showRunCommand = defineCommand({
  path: ["runs", "show"],
  summary: "Show a graph run",
  arguments: [runArgument()],
  examples: [".runs show last", ".runs show 0198..."],
  async execute({ positionals }, context) {
    const run = await loadRun(positionals.run!, context);
    return runRecord(run);
  },
});

export const followRunCommand = defineCommand({
  path: ["runs", "follow"],
  summary: "Follow graph run progress until it settles",
  arguments: [runArgument()],
  examples: [".runs follow last", ".runs follow 0198..."],
  async execute({ positionals }, context) {
    const runId = resolveRunId(positionals.run!, context);
    context.session.lastRunId = runId;
    return {
      kind: "stream",
      events: followResults(
        context.api.runs.follow(runId, context.signal),
        runId,
      ),
    };
  },
});

export const runFailuresCommand = defineCommand({
  path: ["runs", "failures"],
  summary: "Show grouped graph run failures",
  arguments: [runArgument()],
  examples: [".runs failures last", ".runs failures 0198..."],
  async execute({ positionals }, context) {
    const runId = resolveRunId(positionals.run!, context);
    context.session.lastRunId = runId;
    const failures = await context.api.runs.failures(
      runId,
      context.signal,
    );
    return {
      kind: "table",
      columns: [
        "Stage",
        "Code",
        "HTTP",
        "Count",
        "Example URL",
        "Detail",
        "Last seen",
      ],
      rows: failures.items.map((failure) => [
        failure.failure_stage,
        failure.failure_code,
        failure.status_code,
        failure.count,
        failure.example_url,
        failure.example_detail,
        formatTimestamp(failure.last_occurred_at),
      ]),
      summary:
        failures.total === 0
          ? "No crawl failures."
          : `${failures.total} failed ${
              failures.total === 1 ? "request" : "requests"
            }`,
    };
  },
});

export const pauseRunCommand = runControlCommand(
  "pause",
  "Pause graph run admission",
);
export const resumeRunCommand = runControlCommand(
  "resume",
  "Resume a paused graph run",
);
export const cancelRunCommand = runControlCommand(
  "cancel",
  "Cancel a graph run",
);

export const openRunCommand = defineCommand({
  path: ["runs", "open"],
  summary: "Open a graph run in Atlas Web",
  arguments: [runArgument()],
  examples: [".runs open last", ".runs open 0198..."],
  async execute({ positionals }, context) {
    const run = await loadRun(positionals.run!, context);
    return {
      kind: "navigate",
      path: `/crawls/graphs/${encodeURIComponent(run.graph_id)}`,
      label: `Open run ${run.id} in Atlas Web`,
    };
  },
});

function runControlCommand(
  action: "pause" | "resume" | "cancel",
  summary: string,
) {
  return defineCommand({
    path: ["runs", action],
    summary,
    arguments: [runArgument()],
    examples: [`.runs ${action} last`, `.runs ${action} 0198...`],
    async execute({ positionals }, context) {
      const runId = resolveRunId(positionals.run!, context);
      const run = await context.api.runs[action](runId, context.signal);
      context.session.lastRunId = run.id;
      return {
        kind: "message",
        text: `Run ${run.id} is ${run.status.replaceAll("_", " ")}.`,
      };
    },
  });
}

function runCompletion(): CompletionProvider {
  let cached:
    | Promise<Awaited<ReturnType<CommandContext["api"]["runs"]["list"]>>>
    | undefined;
  return async ({ context, cursor, prefix, replaceStart }) => {
    cached ??= context.api.runs.list(context.signal).catch((reason) => {
      cached = undefined;
      throw reason;
    });
    const runs = await cached;
    const normalized = prefix.toLocaleLowerCase();
    const items = runs.items
      .filter((run) => run.id.toLocaleLowerCase().startsWith(normalized))
      .map((run) => ({
        insertText: run.id,
        replaceStart,
        replaceEnd: cursor,
        kind: "argument" as const,
        description: `${run.graph_slug ?? run.graph_id} · ${run.status}`,
      }));
    if ("last".startsWith(normalized) && context.session.lastRunId) {
      items.unshift({
        insertText: "last",
        replaceStart,
        replaceEnd: cursor,
        kind: "argument",
        description: context.session.lastRunId,
      });
    }
    return items;
  };
}

function resolveRunId(value: string, context: CommandContext): string {
  if (value !== "last") return value;
  if (!context.session.lastRunId) {
    throw new ConsoleError(
      "No graph run has been selected in this console session.",
    );
  }
  return context.session.lastRunId;
}

async function loadRun(
  value: string,
  context: CommandContext,
): Promise<GraphRun> {
  const runId = resolveRunId(value, context);
  const run = await context.api.runs.get(runId, context.signal);
  context.session.lastRunId = run.id;
  return run;
}

async function* followResults(
  events: AsyncIterable<GraphRunEvent>,
  runId: string,
): AsyncIterable<AtomicCommandResult> {
  yield { kind: "message", text: `Following run ${runId}…` };
  for await (const event of events) {
    if (event.kind === "settled") {
      yield runRecord(event.run);
      return;
    }
    const nodes = Object.values(event.nodes);
    const edges = Object.values(event.edges);
    const sum = (select: (node: (typeof nodes)[number]) => number) =>
      nodes.reduce((total, node) => total + select(node), 0);
    yield {
      kind: "table",
      columns: [
        "Admitted",
        "Queued",
        "Fetching",
        "Navigating",
        "Completed",
        "Failed",
        "Edges",
        "URLs selected",
      ],
      rows: [[
        sum((node) => node.admitted),
        sum((node) => node.queued),
        sum((node) => node.crawling),
        sum(
          (node) =>
            node.awaiting_navigation + node.evaluating_edges,
        ),
        sum((node) => node.completed),
        sum((node) => node.failed),
        edges.filter((edge) => edge.settled).length + "/" + edges.length,
        edges.reduce((total, edge) => total + edge.urls_selected, 0),
      ]],
    };
  }
}

function runRecord(run: GraphRun): AtomicCommandResult {
  return {
    kind: "table",
    columns: ["Property", "Value"],
    rows: [
      ["Run", run.id],
      ["Graph", run.graph_slug ?? run.graph_id],
      ["Status", run.status.replaceAll("_", " ")],
      ["Trigger", run.trigger_kind],
      ["Root URLs", run.trigger_urls.join(", ")],
      ["Maximum crawls", run.max_crawls],
      ["Requests", run.request_count],
      ["Pending", run.pending_request_count],
      ["Failed", run.failed_request_count],
      ["Errors", run.error_count],
      ["Crawl limit reached", run.crawl_limit_reached ? "yes" : "no"],
      ["Created", formatTimestamp(run.created_at)],
      ["Started", formatTimestamp(run.started_at)],
      ["Completed", formatTimestamp(run.completed_at)],
      ["Error", run.error],
    ],
  };
}

function formatTimestamp(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}
