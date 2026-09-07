import assert from "node:assert/strict"
import { test } from "node:test"
import { queryFailure } from "../src/server/query-failure.ts"

test("Python error categories survive transport and unknown responses cannot become SQL errors", () => {
  assert.deepEqual(queryFailure(503, { code: "storage_unavailable", detail: "Lake storage is unavailable." }), { code: "storage_unavailable", error: "Lake storage is unavailable." })
  assert.equal(queryFailure(422, { code: "sql_invalid", detail: "Check columns" }).code, "sql_invalid")
  assert.equal(queryFailure(408, { code: "resource_limit", detail: "Deadline exceeded" }).code, "resource_limit")
  const unknown = queryFailure(502, { detail: "private token=secret" })
  assert.equal(unknown.code, "service_unavailable")
  assert.equal(JSON.stringify(unknown).includes("secret"), false)
})
