import assert from "node:assert/strict";
import test from "node:test";
import { renderWelcome } from "./welcome.js";

test("the welcome banner identifies Atlas and its schema and compiler", () => {
  const banner = renderWelcome({
    connection: { state: "connected" },
    lakeSlug: "atlas_test",
    schemaVersion: "42",
    compilerVersion: "0.1.0",
    compiler: { state: "idle" },
  });

  assert.match(banner, /___  ________/);
  assert.match(banner, /Lake atlas_test · Atlas schema 42 · compiler 0\.1\.0/);
  assert.match(banner, /The web is messy\. Let's make it queryable\./);
  assert.ok(banner.startsWith("\n"));
  assert.ok(banner.endsWith("\n\n"));
});
