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

test("loads completion metadata through the query operation", async (context) => {
  const originalFetch = globalThis.fetch
  context.after(() => {
    globalThis.fetch = originalFetch
  })
  const statements: string[] = []
  globalThis.fetch = async (input, init) => {
    assert.equal(String(input), "https://periplus.test/api/query/exec")
    assert.equal(init?.method, "POST")
    const body = JSON.parse(String(init?.body)) as { sql: string }
    statements.push(body.sql)
    const responses: Record<string, SqlResult> = {
      "SELECT version() AS duckdb_version": result([["v1.4.0"]]),
      'SHOW TABLES FROM "public_v1"': result([["capture"], ["object"]]),
      'DESCRIBE "public_v1"."capture"': result([
        ["capture_id", "UUID", "NO"],
        ["content_id", "VARCHAR", "YES"],
      ]),
      'DESCRIBE "public_v1"."object"': result([
        ["content_id", "VARCHAR", "NO"],
      ]),
    }
    const response = responses[body.sql]
    assert.ok(response, `unexpected SQL: ${body.sql}`)
    return Response.json(response)
  }

  const metadata = await new SqlApi("https://periplus.test/api").metadata()

  assert.deepEqual(statements, [
    "SELECT version() AS duckdb_version",
    'SHOW TABLES FROM "public_v1"',
    'DESCRIBE "public_v1"."capture"',
    'DESCRIBE "public_v1"."object"',
  ])
  assert.equal(metadata.duckdb_version, "v1.4.0")
  assert.equal(metadata.catalogue_version, undefined)
  assert.deepEqual(metadata.macros, [])
  assert.deepEqual(metadata.relations, [
    {
      schema_name: "public_v1",
      name: "capture",
      kind: "view",
      description: null,
      columns: [
        {
          name: "capture_id",
          data_type: "UUID",
          nullable: false,
          description: null,
        },
        {
          name: "content_id",
          data_type: "VARCHAR",
          nullable: true,
          description: null,
        },
      ],
    },
    {
      schema_name: "public_v1",
      name: "object",
      kind: "view",
      description: null,
      columns: [
        {
          name: "content_id",
          data_type: "VARCHAR",
          nullable: false,
          description: null,
        },
      ],
    },
  ])
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


test("admin SQL uses the privileged endpoint and discovers internal schemas", async (context) => {
  const originalFetch = globalThis.fetch
  context.after(() => { globalThis.fetch = originalFetch })
  const statements: string[] = []
  globalThis.fetch = async (input, init) => {
    assert.equal(String(input), "https://periplus.test/api/admin/sql/exec")
    const { sql } = JSON.parse(String(init?.body)) as { sql: string }
    statements.push(sql)
    if (sql === "SELECT version() AS duckdb_version") return Response.json(result([["v1.5.5"]]))
    if (sql.includes("information_schema.schemata")) return Response.json(result([["ingest"]]))
    if (sql.includes("FROM information_schema.tables")) return Response.json(result([["visits", "BASE TABLE"]]))
    if (sql === 'DESCRIBE "ingest"."visits"') return Response.json(result([["visit_id", "UUID", "NO"]]))
    return Response.json(result([]))
  }
  const api = new SqlApi("https://periplus.test/api", undefined, "admin")
  await api.query("CREATE TABLE operator_test (id INTEGER)")
  const metadata = await api.metadata()
  assert.equal(metadata.relations[0]?.schema_name, "ingest")
  assert.equal(metadata.relations[0]?.kind, "table")
  assert.ok(statements.includes("CREATE TABLE operator_test (id INTEGER)"))
})
