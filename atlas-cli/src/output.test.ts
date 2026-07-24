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
