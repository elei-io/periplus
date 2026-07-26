import assert from "node:assert/strict";
import test from "node:test";
import { argument, defineCommand, option } from "./command.js";
import { CommandRegistry } from "./registry.js";
import type { AtlasApi, CommandContext } from "./types.js";

const api = {
  graphs: { async list() { return { items: [], total: 0 }; } },
  catalogue: {
    async execute() {
      return {
        statementKind: "query" as const,
        columns: [],
        columnTypes: [],
        rows: [],
      };
    },
    async metadata() {
      return {
        catalog_name: "atlas",
        default_schema: "main",
        relations: [],
        functions: [],
      };
    },
    async runtime() {
      return {
        transport: "api" as const,
        mutation_policy: "read_only" as const,
        result_format: "arrow_ipc_stream" as const,
        cancellation_supported: true,
        maximum_concurrency: 4,
        query_timeout_seconds: 30,
        maximum_rows: 1_000,
        maximum_result_bytes: 1_000_000,
      };
    },
    async status() {
      return {
        lake_slug: "atlas_test",
        active_file_count: 0,
        active_storage_bytes: 0,
        ducklake_version: null,
        catalogue_schema_version: "1",
        compiler_version: "0.1.0",
      };
    },
  },
} as unknown as AtlasApi;

const context: CommandContext = {
  api,
  signal: new AbortController().signal,
  session: { history: [] },
  completion: { reload: () => api.catalogue.metadata() },
};

const registry = new CommandRegistry([
  defineCommand({
    path: ["examples", "run"],
    summary: "Exercise declarative completion",
    arguments: [
      argument.choice("mode", ["fast", "faithful"]),
    ],
    options: [
      option.boolean("follow", { description: "Follow the operation" }),
      option.choice("format", ["table", "json"]),
      option.strings("url"),
      option.integer("limit", { minimum: 1, maximum: 100 }),
    ],
    examples: [".examples run fast --format json"],
    execute({ positionals, options }) {
      void positionals;
      void options;
      return { kind: "message", text: "example" };
    },
  }),
]);

test("defineCommand derives canonical usage from arguments and options", () => {
  assert.equal(
    registry.all()[0]?.usage,
    ".examples run <mode> [--follow] [--format <value>] " +
      "[--url <value> ...] [--limit <value>]",
  );
});

test("declared argument choices complete without command-specific code", async () => {
  assert.deepEqual(
    (await registry.complete(".examples run fa", 16, context)).map(
      (item) => item.insertText,
    ),
    ["faithful", "fast"],
  );
});

test("declared options and option choices complete without command-specific code", async () => {
  assert.deepEqual(
    (await registry.complete(".examples run fast --f", 22, context)).map(
      (item) => item.insertText,
    ),
    ["--follow", "--format"],
  );
  assert.deepEqual(
    (await registry.complete(
      ".examples run fast --format j",
      29,
      context,
    )).map((item) => item.insertText),
    ["json"],
  );
});

test("the registry parses declared arguments and options into typed maps", () => {
  const definition = registry.all()[0]!;
  assert.deepEqual(
    registry.invocation(
      definition,
      [
        "fast",
        "--follow",
        "--format",
        "json",
        "--url",
        "https://example.com",
        "--url",
        "https://example.org",
        "--limit",
        "25",
      ],
    ),
    {
      positionals: { mode: "fast" },
      options: {
        follow: true,
        format: "json",
        url: ["https://example.com", "https://example.org"],
        limit: 25,
      },
    },
  );
  assert.throws(
    () => registry.invocation(definition, ["turbo"]),
    /mode must be one of: fast, faithful/,
  );
  assert.throws(
    () => registry.invocation(definition, ["fast", "--limit", "0"]),
    /--limit must be at least 1/,
  );
  assert.throws(
    () => registry.invocation(definition, ["fast", "--limit", "many"]),
    /--limit requires an integer/,
  );
});
