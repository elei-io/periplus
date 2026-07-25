import assert from "node:assert/strict";
import test from "node:test";
import { ConsoleError } from "./errors.js";
import { headlessCommandLine, parseCommandLine } from "./parser.js";

test("interactive commands require a leading dot", () => {
  assert.throws(
    () => parseCommandLine("graphs list"),
    new ConsoleError("Atlas commands start with a dot. Try .help."),
  );
});

test("headless arguments map to the interactive command language", () => {
  assert.equal(headlessCommandLine(["graphs", "list"]), ".graphs list");
  assert.equal(headlessCommandLine([".help"]), ".help");
  assert.equal(
    headlessCommandLine(["select 42 as answer;"]),
    "select 42 as answer;",
  );
});

test("headless commands preserve a multi-word argument", () => {
  assert.equal(
    headlessCommandLine(["ai", "How are book prices distributed?"]),
    '.ai "How are book prices distributed?"',
  );
});

test("quoted command values remain one token", () => {
  assert.deepEqual(parseCommandLine('.help "graphs"'), ["help", "graphs"]);
});
