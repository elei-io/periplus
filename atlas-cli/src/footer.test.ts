import assert from "node:assert/strict";
import test from "node:test";
import { renderFooter } from "./footer.js";

test("the CLI footer renders optimized compiler status", () => {
  const footer = renderFooter({
    connection: { state: "connected" },
    lakeSlug: "atlas_test",
    schemaVersion: "42",
    compilerVersion: "0.1.0",
    compiler: { state: "optimized", rewriteCount: 2 },
  }, 100);

  assert.match(footer, /● connected/);
  assert.match(footer, /lake atlas_test/);
  assert.match(footer, /schema 42/);
  assert.match(footer, /⚡ optimized · 2 rewrites/);
});

test("the CLI footer bounds diagnostics to one terminal row", () => {
  const footer = renderFooter({
    connection: { state: "connected" },
    schemaVersion: "42",
    compiler: {
      state: "diagnostics",
      diagnostics: [{
        code: "unknown_column",
        severity: "error",
        message: "x".repeat(200),
        documentation_anchor: null,
      }],
    },
  }, 40);

  const plain = footer.replace(/\u001b\[[0-9;]*m/g, "");
  assert.ok([...plain].length < 40);
  assert.match(plain, /…$/);
});
