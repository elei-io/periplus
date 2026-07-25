import { ConsoleError } from "../errors.js";
import { argument, defineCommand, option } from "../command.js";
import { resourceCompletion } from "../completion.js";
import type {
  AtlasApi,
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
    option.integer("max-crawls", {
      description: "Maximum pages admitted by the graph run",
      minimum: 1,
      maximum: 1_000_000,
    }),
  ],
  examples: [
    ".graphs run single-page --url https://example.com",
    ".graphs run same-site-depth-1 --url https://example.com --max-crawls 100",
  ],
  async execute({ positionals, options }, context) {
    const graph = await resolveGraph(
      positionals.slug!,
      context.api.graphs.list(context.signal),
    );
    const urls = options.url;
    if (!Array.isArray(urls) || urls.length === 0) {
      throw new ConsoleError(
        "At least one --url is required. Usage: " +
          ".graphs run <slug> --url <value> ... [--max-crawls <value>]",
      );
    }
    for (const url of urls) validateCrawlUrl(url);
    const submission = await context.api.graphs.run(
      graph.id,
      {
        urls: [...urls],
        max_crawls:
          typeof options["max-crawls"] === "number"
            ? options["max-crawls"]
            : undefined,
      },
      context.signal,
    );
    context.session.lastRunId = submission.run_id;
    return {
      kind: "message",
      text:
        `Started ${graph.slug} run ${submission.run_id}.\n` +
        `Follow with .runs follow ${submission.run_id}`,
    };
  },
});

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

function validateCrawlUrl(value: string): void {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new ConsoleError(`Crawl URL must be absolute HTTP(S): ${value}`);
  }
  if (!["http:", "https:"].includes(url.protocol)) {
    throw new ConsoleError(`Crawl URL must be absolute HTTP(S): ${value}`);
  }
}
