import { ConsoleError } from "../errors.js";
import { argument, defineCommand, option } from "../command.js";
import { resourceCompletion } from "../completion.js";
import type {
  AtlasApi,
  CommandContext,
  CompletionProvider,
  CrawlGraphSummary,
} from "../types.js";

export const listGraphsCommand = defineCommand({
  path: ["graphs", "list"],
  summary: "List crawl graphs",
  examples: [".graphs list"],
  async execute(_invocation, context) {
    const graphs = await context.api.graphs.list(context.signal);
    return {
      kind: "table",
      columns: ["Slug", "Description", "Root", "Ownership"],
      rows: graphs.items.map((graph) => [
        graph.slug,
        graph.description ?? "",
        graph.root_node_id ? "configured" : "missing",
        graph.system_owned ? "system" : "user",
      ]),
    };
  },
});

export const showGraphCommand = defineCommand({
  path: ["graphs", "show"],
  summary: "Open a crawl graph in Atlas Web",
  arguments: [
    argument.string("slug", {
      description: "Crawl graph slug",
      complete: graphCompletion(),
    }),
  ],
  examples: [".graphs show single-page"],
  async execute({ positionals }, context) {
    const slug = positionals.slug!;
    const graphs = await context.api.graphs.list(context.signal);
    const graph = graphs.items.find((candidate) => candidate.slug === slug);
    if (!graph) throw new ConsoleError(`Crawl graph not found: ${slug}`);
    return {
      kind: "navigate",
      path: `/crawls/graphs/${encodeURIComponent(graph.id)}`,
      label: `Open ${graph.slug} in Atlas Web`,
    };
  },
});

export const runGraphCommand = defineCommand({
  path: ["graphs", "run"],
  summary: "Start a crawl graph run",
  arguments: [
    argument.string("slug", {
      description: "Crawl graph slug",
      complete: graphCompletion(),
    }),
  ],
  options: [
    option.strings("url", {
      description: "Root URL; may be repeated",
    }),
    option.string("from-result", {
      description: "Column from the latest SQL result containing root URLs",
    }),
    option.integer("max-crawls", {
      description: "Maximum pages admitted by the graph run",
      minimum: 1,
      maximum: 1_000_000,
    }),
  ],
  examples: [
    ".graphs run single-page --url https://example.com",
    ".graphs run single-page --from-result url --max-crawls 100",
    ".graphs run same-site-depth-1 --url https://example.com --max-crawls 100",
  ],
  async execute({ positionals, options }, context) {
    const graph = await resolveGraph(
      positionals.slug!,
      context.api.graphs.list(context.signal),
    );
    const explicitUrls = options.url;
    const resultColumn = options["from-result"];
    if (Array.isArray(explicitUrls) && typeof resultColumn === "string") {
      throw new ConsoleError("--url and --from-result cannot be used together.");
    }
    if (
      (!Array.isArray(explicitUrls) || explicitUrls.length === 0) &&
      typeof resultColumn !== "string"
    ) {
      throw new ConsoleError(
        "Provide --url or --from-result. Usage: " +
          ".graphs run <slug> [--url <value> ... | --from-result <column>] " +
          "[--max-crawls <value>]",
      );
    }
    const extracted =
      typeof resultColumn === "string"
        ? await urlsFromQuery(
            context.session.lastSqlQuery,
            resultColumn,
            context,
          )
        : normalizeExplicitUrls(explicitUrls as readonly string[]);
    const maxCrawls =
      typeof options["max-crawls"] === "number"
        ? options["max-crawls"]
        : undefined;
    if (maxCrawls !== undefined && maxCrawls < extracted.urls.length) {
      throw new ConsoleError(
        `--max-crawls must be at least the ${extracted.urls.length} root URLs.`,
      );
    }
    context.session.pendingGraphSubmission = {
      graphId: graph.id,
      graphSlug: graph.slug,
      urls: extracted.urls,
      maxCrawls,
    };
    return {
      kind: "message",
      text: confirmationMessage(graph.slug, extracted),
    };
  },
});

export const confirmGraphRunCommand = defineCommand({
  path: ["graphs", "confirm"],
  summary: "Confirm the pending crawl graph run",
  examples: [".graphs confirm"],
  async execute(_invocation, context) {
    const pending = context.session.pendingGraphSubmission;
    if (!pending) {
      throw new ConsoleError("There is no pending graph run to confirm.");
    }
    context.session.pendingGraphSubmission = undefined;
    const submission = await context.api.graphs.run(
      pending.graphId,
      {
        urls: pending.urls,
        max_crawls: pending.maxCrawls,
      },
      context.signal,
    );
    context.session.lastRunId = submission.run_id;
    return {
      kind: "message",
      text:
        `Started ${pending.graphSlug} run ${submission.run_id}.\n` +
        `Follow with .runs follow ${submission.run_id}`,
    };
  },
});

async function urlsFromQuery(
  query: { sql: string } | undefined,
  requestedColumn: string,
  context: CommandContext,
): Promise<UrlExtraction> {
  if (!query) {
    throw new ConsoleError(
      "No SQL query is available. Run a query or `.ai N` first.",
    );
  }
  const result = await context.api.catalogue.execute(
    query.sql,
    context.signal,
  );
  const matches = result.columns
    .map((column, index) => ({ column, index }))
    .filter(
      ({ column }) =>
        column.toLocaleLowerCase() === requestedColumn.toLocaleLowerCase(),
    );
  if (matches.length !== 1) {
    throw new ConsoleError(
      matches.length === 0
        ? `Result column not found: ${requestedColumn}`
        : `Result column is ambiguous: ${requestedColumn}`,
    );
  }
  return normalizeUrls(result.rows.map((row) => row[matches[0]!.index]));
}

interface UrlExtraction {
  urls: string[];
  duplicateCount: number;
  invalidCount: number;
}

function normalizeExplicitUrls(values: readonly string[]): UrlExtraction {
  return normalizeUrls(values.map((value) => validateCrawlUrl(value)));
}

function normalizeUrls(values: readonly unknown[]): UrlExtraction {
  const urls: string[] = [];
  const seen = new Set<string>();
  let duplicateCount = 0;
  let invalidCount = 0;
  for (const value of values) {
    if (typeof value !== "string") {
      invalidCount += 1;
      continue;
    }
    let normalized: string;
    try {
      normalized = validateCrawlUrl(value);
    } catch {
      invalidCount += 1;
      continue;
    }
    if (seen.has(normalized)) {
      duplicateCount += 1;
      continue;
    }
    seen.add(normalized);
    urls.push(normalized);
  }
  if (urls.length === 0) {
    throw new ConsoleError("The selected values contain no valid HTTP(S) URLs.");
  }
  if (urls.length > 10_000) {
    throw new ConsoleError("A graph run accepts at most 10,000 root URLs.");
  }
  return { urls, duplicateCount, invalidCount };
}

function confirmationMessage(
  graphSlug: string,
  extraction: UrlExtraction,
): string {
  const sample = extraction.urls
    .slice(0, 5)
    .map((url) => `  ${url}`)
    .join("\n");
  return (
    `Ready to start ${graphSlug} with ${extraction.urls.length} distinct root ` +
    `${extraction.urls.length === 1 ? "URL" : "URLs"}.\n` +
    `Duplicates removed: ${extraction.duplicateCount} · Invalid ignored: ` +
    `${extraction.invalidCount}\nSample:\n${sample}\n` +
    "Confirm with .graphs confirm"
  );
}

function graphCompletion(): CompletionProvider {
  return resourceCompletion<CrawlGraphSummary>({
    load: async ({ context }) =>
      (await context.api.graphs.list(context.signal)).items,
    value: (graph) => graph.slug,
    description: (graph) => graph.description ?? undefined,
    kind: "argument",
  });
}

async function resolveGraph(
  slug: string,
  pending: ReturnType<AtlasApi["graphs"]["list"]>,
): Promise<CrawlGraphSummary> {
  const graphs = await pending;
  const graph = graphs.items.find((candidate) => candidate.slug === slug);
  if (!graph) throw new ConsoleError(`Crawl graph not found: ${slug}`);
  return graph;
}

function validateCrawlUrl(value: string): string {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new ConsoleError(`Crawl URL must be absolute HTTP(S): ${value}`);
  }
  if (!["http:", "https:"].includes(url.protocol)) {
    throw new ConsoleError(`Crawl URL must be absolute HTTP(S): ${value}`);
  }
  return url.href;
}
