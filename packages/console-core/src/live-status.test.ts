import assert from "node:assert/strict";
import test from "node:test";
import { LiveConsoleStatus } from "./live-status.js";
import type {
  AtlasApi,
  CatalogueCompilationResult,
} from "./types.js";

function compilation(
  change: Partial<CatalogueCompilationResult> = {},
): CatalogueCompilationResult {
  return {
    valid: true,
    supported: true,
    materialization_eligible: false,
    outcome: "unchanged",
    authored_sql: "SELECT 1",
    executable_sql: "SELECT 1",
    diagnostics: [],
    applied_rewrites: [],
    catalogue_revision: "revision-1",
    compiler_version: "1",
    ...change,
  };
}

function api(
  compile: AtlasApi["catalogue"]["compile"],
): AtlasApi {
  return {
    catalogue: {
      compile,
      async status() {
        return {
          lake_slug: "atlas_test",
          active_file_count: 0,
          active_storage_bytes: 0,
          ducklake_version: null,
          catalogue_schema_version: "42",
          compiler_version: "0.1.0",
        };
      },
    },
  } as unknown as AtlasApi;
}

test("live status connects and distinguishes valid and optimized SQL", async () => {
  let result = compilation();
  const status = new LiveConsoleStatus(
    api(async () => result),
    0,
  );

  await status.connect();
  assert.deepEqual(status.snapshot().connection, { state: "connected" });
  assert.equal(status.snapshot().schemaVersion, "42");

  status.updateInput("SELECT 1");
  await settle();
  assert.deepEqual(status.snapshot().compiler, { state: "valid" });

  result = compilation({
    outcome: "optimized",
    applied_rewrites: [{ rule: "pushdown", evidence: "bounded scan" }],
  });
  status.updateInput("SELECT * FROM crawl.pages");
  await settle();
  assert.deepEqual(status.snapshot().compiler, {
    state: "optimized",
    rewriteCount: 1,
  });
});

test("new input aborts stale compiler feedback", async () => {
  let firstSignal: AbortSignal | undefined;
  const status = new LiveConsoleStatus(
    api(async (sql, signal) => {
      if (sql === "SELECT old") {
        firstSignal = signal;
        await new Promise(() => {});
      }
      return compilation({ authored_sql: sql });
    }),
    0,
  );

  status.updateInput("SELECT old");
  await settle();
  status.updateInput("SELECT new");
  await settle();

  assert.equal(firstSignal?.aborted, true);
  assert.deepEqual(status.snapshot().compiler, { state: "valid" });
});

function settle(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 5));
}
