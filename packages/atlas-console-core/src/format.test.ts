import assert from "node:assert/strict"
import test from "node:test"

import { formatSqlResult } from "./format.js"

test("formats a bounded table with row count and duration", () => {
  const output = formatSqlResult(
    {
      columns: ["name", "note"],
      types: ["VARCHAR", "VARCHAR"],
      rows: [["Atlas", "the web\nas tables"]],
      truncated: false,
    },
    80,
    1_234,
  )

  assert.deepEqual(output.table, [
    "name   note",
    "─────  ─────────────────",
    "Atlas  the web as tables",
  ])
  assert.equal(output.summary, "1 row · 1.2s")
})
