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
    assert.equal(String(input), "https://periplus.test/api/sql/query")
    assert.equal(init?.method, "POST")
    const body = JSON.parse(String(init?.body)) as { sql: string }
    statements.push(body.sql)
    const responses: Record<string, SqlResult> = {
      "SELECT version() AS duckdb_version": result([["v1.4.0"]]),
      "SHOW TABLES FROM web": result([["observation"]]),
      'DESCRIBE web."observation"': result([
        ["observation_id", "UUID", "NO"],
        ["content_id", "VARCHAR", "YES"],
      ]),
      "SHOW TABLES FROM content": result([["object"]]),
      'DESCRIBE content."object"': result([
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
    "SHOW TABLES FROM web",
    'DESCRIBE web."observation"',
    "SHOW TABLES FROM content",
    'DESCRIBE content."object"',
  ])
  assert.equal(metadata.duckdb_version, "v1.4.0")
  assert.equal(metadata.catalogue_version, undefined)
  assert.deepEqual(metadata.macros, [])
  assert.deepEqual(metadata.relations, [
    {
      schema_name: "web",
      name: "observation",
      kind: "view",
      description: null,
      columns: [
        {
          name: "observation_id",
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
      schema_name: "content",
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
