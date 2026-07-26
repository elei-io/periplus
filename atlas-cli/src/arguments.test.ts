import assert from "node:assert/strict";
import test from "node:test";
import { parseArguments } from "./arguments.js";

test("plain command arguments select headless mode", () => {
  assert.deepEqual(parseArguments(["graphs", "list"]), {
    apiUrl: undefined,
    webUrl: undefined,
    format: "table",
    command: ["graphs", "list"],
  });
});

test("global options are removed from the command", () => {
  assert.deepEqual(
    parseArguments([
      "--api-url",
      "http://localhost:8000",
      "--format",
      "json",
      "--web-url",
      "http://localhost:8080",
      "graphs",
      "list",
    ]),
    {
      apiUrl: "http://localhost:8000",
      webUrl: "http://localhost:8080",
      format: "json",
      command: ["graphs", "list"],
    },
  );
});
