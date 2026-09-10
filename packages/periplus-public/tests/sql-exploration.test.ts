import assert from "node:assert/strict"
import test from "node:test"
import { createSqlExplorer } from "../src/server/sql-exploration.ts"

test("SQL exploration forwards parameters and marks sampled evidence", async () => {
  const original = globalThis.fetch
  globalThis.fetch = async (_url, init) => {
    assert.deepEqual(JSON.parse(String(init?.body)), { sql: "SELECT ?", parameters: [1] })
    return Response.json({ sql: "SELECT ?", columns: ["n"], types: ["INTEGER"], rows: Array.from({ length: 101 }, (_, i) => [i]), query_id: "q", source_snapshot: 7, truncated: false })
  }
  try {
    const result = await createSqlExplorer("http://query", {}, new AbortController().signal)("SELECT ?", [1])
    assert.ok("rows" in result)
    assert.ok(result.rows)
    assert.equal(result.rows.length, 100)
    assert.equal(result.sampled, true)
    assert.equal(result.source_snapshot, 7)
  } finally { globalThis.fetch = original }
})
test("service failure prevents repeated SQL calls in the same exploration", async () => {
  const original = globalThis.fetch
  let calls = 0
  let unavailable = 0
  globalThis.fetch = async () => { calls++; return Response.json({}, { status: 429 }) }
  try {
    const run = createSqlExplorer("http://query", {}, new AbortController().signal, () => { unavailable++ })
    await run("SELECT 1", [])
    await run("SELECT 2", [])
    assert.equal(calls, 1)
    assert.equal(unavailable, 1)
  } finally { globalThis.fetch = original }
})
