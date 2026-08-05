import assert from "node:assert/strict"
import test from "node:test"

import {
  formatSqlResult,
  renderFormattedTable,
} from "./format.js"

test("formats a bounded table with row count and duration", () => {
  const output = formatSqlResult(
    {
      columns: ["name", "note"],
      types: ["VARCHAR", "VARCHAR"],
      rows: [["Periplus", "the web\nas tables"]],
      truncated: false,
    },
    80,
    1_234,
  )

  assert.deepEqual(output.table, [
    "name      note",
    "────────  ─────────────────",
    "Periplus  the web as tables",
  ])
  assert.equal(output.summary, "1 row · 1.2s")
})

test("sanitizes control characters and formats binary values", () => {
  const output = formatSqlResult(
    {
      columns: ["value"],
      types: ["VARCHAR"],
      rows: [["safe\u001b[2Jtext"], [new Uint8Array([10, 255])]],
      truncated: false,
    },
    80,
    4,
  )

  assert.deepEqual(output.table.slice(2), ["safetext", "0x0aff"])
})

test("limits terminal display independently from the server result cap", () => {
  const output = formatSqlResult(
    {
      columns: ["value"],
      types: ["INTEGER"],
      rows: Array.from({ length: 250 }, (_, index) => [index]),
      truncated: true,
    },
    80,
    4,
  )

  assert.equal(output.table.length, 102)
  assert.equal(
    output.summary,
    "Showing 100 of 250 returned rows (server result truncated) · 4ms",
  )
})

test("styles headers, separators, nulls, and summaries", () => {
  const lines = renderFormattedTable({
    table: ["value", "─────", "NULL"],
    summary: "1 row",
  })

  assert.match(lines[0]!, /\u001b\[1;34mvalue/)
  assert.match(lines[1]!, /\u001b\[2m─────/)
  assert.match(lines[2]!, /\u001b\[33mNULL/)
  assert.match(lines[3]!, /\u001b\[2m1 row/)
})
