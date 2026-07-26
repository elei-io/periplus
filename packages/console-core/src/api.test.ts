import assert from "node:assert/strict";
import test from "node:test";
import { HttpAtlasApi } from "./api.js";

test("graph-run API maps mutations and parses progress events", async () => {
  const requests: Array<{ path: string; method: string; body: unknown }> = [];
  const request: typeof fetch = async (input, init) => {
    const url = new URL(
      input instanceof Request ? input.url : input.toString(),
    );
    requests.push({
      path: url.pathname,
      method: init?.method ?? "GET",
      body:
        typeof init?.body === "string"
          ? JSON.parse(init.body) as unknown
          : undefined,
    });
    if (url.pathname.endsWith("/events")) {
      return new Response(
        "event: progress_snapshot\n" +
          'data: {"nodes":{},"edges":{}}\n\n' +
          "event: run_settled\n" +
          'data: {"id":"run-1","graph_id":"graph-1","status":"completed"}\n\n',
        { headers: { "Content-Type": "text/event-stream" } },
      );
    }
    return Response.json({
      graph_id: "graph-1",
      run_id: "run-1",
      status: "queued",
    });
  };
  const api = new HttpAtlasApi("http://atlas.test/api", request);

  await api.graphs.run("graph-1", {
    urls: ["https://example.com/"],
    max_crawls: 1,
  });
  const events = [];
  for await (const event of api.runs.follow("run-1")) events.push(event);

  assert.deepEqual(requests, [
    {
      path: "/api/crawl-graphs/graph-1/runs",
      method: "POST",
      body: { urls: ["https://example.com/"], max_crawls: 1 },
    },
    {
      path: "/api/graph-runs/run-1/events",
      method: "GET",
      body: undefined,
    },
  ]);
  assert.deepEqual(events[0], { kind: "progress", nodes: {}, edges: {} });
  assert.equal(events[1]?.kind, "settled");
});

test("catalogue compilation uses the interactive compiler contract", async () => {
  let requestBody: unknown;
  const request: typeof fetch = async (_input, init) => {
    requestBody = JSON.parse(String(init?.body)) as unknown;
    return Response.json({
      valid: true,
      supported: true,
      materialization_eligible: false,
      outcome: "optimized",
      authored_sql: "SELECT 1",
      executable_sql: "SELECT 1",
      diagnostics: [],
      applied_rewrites: [],
      dependencies: [],
      catalogue_revision: "revision-1",
      compiler_version: "1",
    });
  };
  const api = new HttpAtlasApi("http://atlas.test/api", request);

  const result = await api.catalogue.compile("SELECT 1");

  assert.deepEqual(requestBody, {
    sql: "SELECT 1",
    purpose: "interactive",
  });
  assert.equal(result.outcome, "optimized");
});
