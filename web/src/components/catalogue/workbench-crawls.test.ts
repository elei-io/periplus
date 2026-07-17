import assert from "node:assert/strict"
import test from "node:test"

import {
  crawlColumnCandidates,
  parseCrawlCommandTarget,
  selectionFromNamedColumn,
  selectionFromUrl,
} from "./workbench-crawls.ts"

test("parses direct and last-result crawl commands", () => {
  assert.deepEqual(parseCrawlCommandTarget('--graph "Product discovery"'), {
    kind: "last-result",
    graph: "Product discovery",
    column: null,
  })
  assert.deepEqual(
    parseCrawlCommandTarget(
      "https://example.com/products --graph=67cd43bd-2c06-4987-900d-a5cb73cc542e"
    ),
    {
      kind: "url",
      graph: "67cd43bd-2c06-4987-900d-a5cb73cc542e",
      url: "https://example.com/products",
    }
  )
  assert.deepEqual(
    parseCrawlCommandTarget(
      '--graph "Product discovery" --column "canonical url"'
    ),
    {
      kind: "last-result",
      graph: "Product discovery",
      column: "canonical url",
    }
  )
  assert.deepEqual(parseCrawlCommandTarget("https://example.com/products"), {
    kind: "url",
    graph: "single-page",
    url: "https://example.com/products",
  })
  assert.deepEqual(parseCrawlCommandTarget("--graph products --column"), {
    kind: "error",
    error: "Usage: \\crawl [--graph <graph>] [<url> | --column <column>]",
  })
  assert.deepEqual(parseCrawlCommandTarget('--graph "products'), {
    kind: "error",
    error: "Unterminated quote in crawl command.",
  })
  assert.deepEqual(parseCrawlCommandTarget(""), {
    kind: "last-result",
    graph: "single-page",
    column: null,
  })
})

test("accepts only direct HTTP and HTTPS URLs", () => {
  assert.deepEqual(selectionFromUrl(" https://example.com "), {
    column: null,
    urls: ["https://example.com"],
    valueCount: 1,
    invalidCount: 0,
    duplicateCount: 0,
  })
  assert.deepEqual(selectionFromUrl("file:///tmp/page.html"), {
    error: "Crawl URLs must use HTTP or HTTPS.",
  })
})

test("detects URL columns and reports excluded values", () => {
  const result = {
    statementKind: "query" as const,
    columns: ["title", "canonical_url", "destination"],
    columnTypes: ["VARCHAR", "VARCHAR", "VARCHAR"],
    rows: [
      ["One", "https://example.com/one", "https://other.example/one"],
      ["Two", "https://example.com/one", "https://other.example/two"],
      ["Three", "not a URL", "https://other.example/three"],
      ["Four", null, "https://other.example/four"],
    ],
  }

  const candidates = crawlColumnCandidates(result)
  assert.deepEqual(
    candidates.map(({ column, urls }) => ({ column, urls })),
    [
      {
        column: "canonical_url",
        urls: ["https://example.com/one"],
      },
      {
        column: "destination",
        urls: [
          "https://other.example/one",
          "https://other.example/two",
          "https://other.example/three",
          "https://other.example/four",
        ],
      },
    ]
  )
  assert.deepEqual(selectionFromNamedColumn(result, "CANONICAL_URL"), {
    column: "canonical_url",
    urls: ["https://example.com/one"],
    valueCount: 3,
    invalidCount: 1,
    duplicateCount: 1,
  })
})
