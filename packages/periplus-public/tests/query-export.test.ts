import assert from "node:assert/strict"
import { test } from "node:test"
import { serializeQueryResults } from "../src/lib/query-export.ts"

test("JSON preserves typed values, nested data and duplicate column names", () => {
  const result = { columns: ["value", "value", "flag", "missing", "nested"], types: ["VARCHAR", "INTEGER", "BOOLEAN", "VARCHAR", "JSON"], rows: [["42", 42, false, null, { a: [1, true] }]] }
  assert.deepEqual(JSON.parse(serializeQueryResults(result, "json")), result)
})
test("CSV quotes headers, commas, quotes and newlines and renders complex values", () => {
  assert.equal(serializeQueryResults({ columns: ['a"b', "c"], types: [], rows: [["line\nbreak,quoted", { x: 1 }], [null, false]] }, "csv"), '"a""b","c"\r\n"line\nbreak,quoted","{""x"":1}"\r\n"NULL","false"')
})
test("empty results keep headers and JSON schema", () => {
  const result = { columns: ["id"], types: ["INTEGER"], rows: [] }
  assert.equal(serializeQueryResults(result, "csv"), '"id"')
  assert.deepEqual(JSON.parse(serializeQueryResults(result, "json")), result)
})
