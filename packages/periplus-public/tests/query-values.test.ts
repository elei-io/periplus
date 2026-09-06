import assert from "node:assert/strict"
import { test } from "node:test"
import { displayValue, formatQueryTimestamp } from "../src/lib/query-values.ts"

test("query cells preserve big integers and nested values without losing precision", () => {
  assert.equal(displayValue(BigInt("9007199254740993")), "9007199254740993")
  assert.equal(displayValue({ id: BigInt("9007199254740993"), tags: ["a", null] }), '{"id":"9007199254740993","tags":["a",null]}')
  assert.equal(displayValue(null), "NULL")
  assert.equal(displayValue(false), "false")
  assert.equal(displayValue(0), "0")
})

test("readable timestamps distinguish UTC instants from wall-clock values", () => {
  assert.deepEqual(formatQueryTimestamp("2026-08-14T14:37:39.469430+09:00", "TIMESTAMP WITH TIME ZONE"), {
    date: "14 Aug 2026", time: "05:37:39 UTC", dateTime: "2026-08-14T05:37:39.469Z",
  })
  assert.deepEqual(formatQueryTimestamp("2026-08-14 14:37:39.469430", "TIMESTAMP"), {
    date: "14 Aug 2026", time: "14:37:39", dateTime: "2026-08-14T14:37:39.469430",
  })
  assert.deepEqual(formatQueryTimestamp("2026-08-14", "DATE"), {
    date: "14 Aug 2026", time: null, dateTime: "2026-08-14",
  })
  assert.equal(formatQueryTimestamp("infinity", "TIMESTAMP"), null)
  assert.equal(formatQueryTimestamp("2026-08-14", "VARCHAR"), null)
})
