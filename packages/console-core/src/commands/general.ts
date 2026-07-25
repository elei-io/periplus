import { argument, defineCommand } from "../command.js";
import { ConsoleError } from "../errors.js";

export const clearCommand = defineCommand({
  path: ["clear"],
  summary: "Clear the console",
  examples: [".clear"],
  execute() {
    return { kind: "clear" };
  },
});

export const historyCommand = defineCommand({
  path: ["history"],
  summary: "Show console input history",
  examples: [".history"],
  execute(_invocation, context) {
    return {
      kind: "table",
      columns: ["#", "Input"],
      rows: context.session.history.map((input, index) => [index + 1, input]),
    };
  },
});

export const statusCommand = defineCommand({
  path: ["status"],
  summary: "Show catalogue runtime and limits",
  examples: [".status"],
  async execute(_invocation, context) {
    const startedAt = performance.now();
    const [status, runtime] = await Promise.all([
      context.api.catalogue.status(context.signal),
      context.api.catalogue.runtime(context.signal),
    ]);
    return {
      kind: "table",
      columns: ["Property", "Value"],
      rows: [
        ["Lake", status.lake_slug],
        ["Transport", runtime.transport],
        ["Mutation policy", runtime.mutation_policy],
        ["Result format", runtime.result_format],
        ["Cancellation", runtime.cancellation_supported ? "supported" : "unavailable"],
        ["Maximum concurrency", runtime.maximum_concurrency],
        ["Query timeout", `${runtime.query_timeout_seconds}s`],
        ["Maximum rows", runtime.maximum_rows],
        ["Maximum result size", formatBytes(runtime.maximum_result_bytes)],
        ["Active Parquet files", status.active_file_count],
        ["Catalogue storage", formatBytes(status.active_storage_bytes)],
        ["DuckLake version", status.ducklake_version ?? "unknown"],
        ["Atlas schema", status.catalogue_schema_version],
        ["Status latency", `${Math.max(1, Math.round(performance.now() - startedAt))}ms`],
      ],
    };
  },
});

export const describeCommand = defineCommand({
  path: ["describe"],
  summary: "Describe a catalogue relation",
  arguments: [
    argument.string("relation", {
      description: "Table or view name",
      complete: async ({ context, cursor, prefix, replaceStart }) => {
        const metadata = await context.api.catalogue.metadata(context.signal);
        return metadata.relations
          .flatMap((relation) => [
            relation.name,
            `${relation.schema_name}.${relation.name}`,
          ])
          .filter((name) =>
            name.toLocaleLowerCase().startsWith(prefix.toLocaleLowerCase()),
          )
          .map((name) => ({
            insertText: name,
            replaceStart,
            replaceEnd: cursor,
            kind: "relation" as const,
          }));
      },
    }),
  ],
  examples: [".describe elements"],
  async execute({ positionals }, context) {
    const target = positionals.relation!;
    const metadata = await context.api.catalogue.metadata(context.signal);
    const normalized = target.toLocaleLowerCase();
    const matches = metadata.relations.filter((relation) =>
      relation.name.toLocaleLowerCase() === normalized ||
      `${relation.schema_name}.${relation.name}`.toLocaleLowerCase() === normalized,
    );
    if (matches.length === 0) {
      throw new ConsoleError(`Catalogue relation not found: ${target}`);
    }
    if (matches.length > 1) {
      throw new ConsoleError(
        `Catalogue relation is ambiguous: ${target}. Include its schema.`,
      );
    }
    const relation = matches[0]!;
    return {
      kind: "table",
      columns: ["Column", "Type", "Nullable"],
      rows: relation.columns.map((column) => [
        column.name,
        column.data_type,
        column.nullable ? "yes" : "no",
      ]),
      summary: `${relation.schema_name}.${relation.name} · ${relation.kind}`,
    };
  },
});

export const reloadCompletionCommand = defineCommand({
  path: ["completion", "reload"],
  summary: "Reload catalogue completion metadata",
  examples: [".completion reload"],
  async execute(_invocation, context) {
    const metadata = await context.completion.reload(context.signal);
    const columnCount = metadata.relations.reduce(
      (total, relation) => total + relation.columns.length,
      0,
    );
    return {
      kind: "message",
      text: `Completion metadata reloaded: ${metadata.relations.length} relations, ${columnCount} columns, ${metadata.functions.length} functions.`,
    };
  },
});

function formatBytes(value: number): string {
  if (value < 1_024) return `${value} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let amount = value;
  let unit = -1;
  do {
    amount /= 1_024;
    unit += 1;
  } while (amount >= 1_024 && unit < units.length - 1);
  return `${amount.toFixed(amount >= 10 ? 1 : 2)} ${units[unit]}`;
}
