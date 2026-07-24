import assert from "node:assert/strict";
import test from "node:test";
import { AtlasConsole } from "./console.js";
import type { AtlasApi } from "./types.js";

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
    },
  };
}

test(".help is generated from the command registry", async () => {
  const result = await new AtlasConsole(api()).execute(".help");
  assert.deepEqual(result, {
    kind: "table",
    columns: ["Command", "Description"],
    rows: [
      [".help [resource]", "Show available Atlas commands"],
      [".clear", "Clear the console"],
      [".graphs list", "List crawl graphs"],
      [".graphs show <slug>", "Open a crawl graph in Atlas Web"],
    ],
  });
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
