import assert from "node:assert/strict"
import test from "node:test"

import { SqlApi } from "./api.js"
import type { SqlResult } from "./types.js"

function result(rows: unknown[][]): SqlResult {
  return {
    columns: [],
    types: [],
    rows,
    truncated: false,
  }
}

test("loads native public column metadata without unsupported SHOW or DESCRIBE", async (context) => {
  const originalFetch = globalThis.fetch
  context.after(() => { globalThis.fetch = originalFetch })
  const statements: string[] = []
  globalThis.fetch = async (_input, init) => {
    const { sql } = JSON.parse(String(init?.body)) as { sql: string }
    statements.push(sql)
    return Response.json(sql === "SELECT version()" ? result([["26.8"]]) : {
      columns: ["id"], types: ["Nullable(String)"], rows: [], truncated: false,
    })
  }
  const metadata = await new SqlApi("https://periplus.test/api").metadata()
  assert.equal(metadata.engine_version, "26.8")
  assert.equal(metadata.relations.length, 4)
  assert.equal(metadata.relations[0]?.columns[0]?.nullable, true)
  assert.equal(statements.length, 5)
})

test("rejects an incompatible query response", async (context) => {
  const originalFetch = globalThis.fetch
  context.after(() => {
    globalThis.fetch = originalFetch
  })
  globalThis.fetch = async () => Response.json({ rows: [] })

  await assert.rejects(
    new SqlApi("https://periplus.test/api").query("SELECT 1"),
    /incompatible SQL query contract/
  )
})


test("admin uses the privileged SQL and metadata endpoints", async (context) => {
  const originalFetch = globalThis.fetch
  context.after(() => { globalThis.fetch = originalFetch })
  globalThis.fetch = async (input) => {
    if (String(input).endsWith("/sql/metadata")) return Response.json({ engine_version: "26.8", relations: [], macros: [] })
    assert.equal(String(input), "https://periplus.test/api/admin/sql/exec")
    return Response.json(result([[1]]))
  }
  const api = new SqlApi("https://periplus.test/api", undefined, "admin")
  assert.deepEqual((await api.query("SELECT 1")).rows, [[1]])
  assert.equal((await api.metadata()).engine_version, "26.8")
})
