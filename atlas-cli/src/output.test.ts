import assert from "node:assert/strict";
import test from "node:test";
import { renderResult } from "./output.js";

test("table results render for a terminal", () => {
  assert.equal(
    renderResult(
      {
        kind: "table",
        columns: ["Command", "Description"],
        rows: [[".help", "Show help"]],
      },
      { format: "table", columns: 80 },
    ),
    "Command  Description\n───────  ───────────\n.help    Show help\n",
  );
});

test("structured results remain structured in JSON mode", () => {
  const result = {
    kind: "message" as const,
    text: "Ready",
  };
  assert.equal(
    renderResult(result, { format: "json" }),
    `${JSON.stringify(result, null, 2)}\n`,
  );
});

test("navigation results render their user-facing action", () => {
  assert.equal(
    renderResult(
      {
        kind: "navigate",
        path: "/crawls/graphs/graph-1",
        label: "Open single-page in Atlas Web",
      },
      { format: "table" },
    ),
    "Open single-page in Atlas Web\n",
  );
});

test("clear results render the terminal clear sequence", () => {
  assert.equal(
    renderResult({ kind: "clear" }, { format: "table" }),
    "\u001b[2J\u001b[H",
  );
});

test("assistant answers terminate their transcript rail", () => {
  const output = renderResult(
    {
      kind: "assistant",
      text: "The lake contains retained book data.",
    },
    { format: "table" },
  );
  assert.match(output, /└ .*The lake contains retained book data\./);
  assert.ok(output.startsWith("\u001b[2m│"));
  assert.ok(output.endsWith("\n\n"));
});

test("assistant suggestions render metadata without their SQL", () => {
  const output = renderResult(
    {
      kind: "assistant",
      text: "Try one of these.",
      suggestions: [
        {
          index: 2,
          title: "Price distribution",
          description: "Summarise prices by percentile.",
        },
      ],
    },
    { format: "table" },
  );
  assert.match(output, /2\..*Price distribution/);
  assert.match(output, /\.ai <number> · --copy · --show/);
  assert.doesNotMatch(output, /SELECT/);
});

test("tables distinguish nulls and serialize nested and large values", () => {
  assert.equal(
    renderResult(
      {
        kind: "table",
        columns: ["null", "array", "nested", "large"],
        rows: [[null, [1, "two"], { ready: true }, 9_007_199_254_740_993n]],
        summary: "1 row · 12ms",
      },
      { format: "table", columns: 120 },
    ),
    "null  array      nested          large\n" +
      "────  ─────────  ──────────────  ────────────────\n" +
      'NULL  [1,\"two\"]  {\"ready\":true}  9007199254740993\n' +
      "1 row · 12ms\n",
  );
});
