import assert from "node:assert/strict";
import test from "node:test";
import { resourceUrl } from "./browser.js";

test("resource paths resolve against the configured Atlas Web origin", () => {
  assert.equal(
    resourceUrl(
      "https://atlas.example.com/",
      "/crawls/graphs/graph-1",
    ),
    "https://atlas.example.com/crawls/graphs/graph-1",
  );
});
