import { ConsoleError } from "../errors.js";
import type { CommandDefinition } from "../types.js";

export const listGraphsCommand: CommandDefinition = {
  path: ["graphs", "list"],
  summary: "List crawl graphs",
  usage: ".graphs list",
  examples: [".graphs list"],
  async execute({ args }, context) {
    if (args.length) throw new ConsoleError("Usage: .graphs list");
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
};

export const showGraphCommand: CommandDefinition = {
  path: ["graphs", "show"],
  summary: "Open a crawl graph in Atlas Web",
  usage: ".graphs show <slug>",
  examples: [".graphs show single-page"],
  async execute({ args }, context) {
    if (args.length !== 1) {
      throw new ConsoleError("Usage: .graphs show <slug>");
    }
    const slug = args[0]!;
    const graphs = await context.api.graphs.list(context.signal);
    const graph = graphs.items.find((candidate) => candidate.slug === slug);
    if (!graph) throw new ConsoleError(`Crawl graph not found: ${slug}`);
    return {
      kind: "navigate",
      path: `/crawls/graphs/${encodeURIComponent(graph.id)}`,
      label: `Open ${graph.slug} in Atlas Web`,
    };
  },
};
