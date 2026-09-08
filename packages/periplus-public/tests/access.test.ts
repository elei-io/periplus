import test from "node:test"
import assert from "node:assert/strict"
import { parseAccessPolicy } from "../src/types/access.ts"

const rate = { enabled: true, requests: 60, window_seconds: 60 }
const policy = {
  version: 3,
  crawl: { ...rate, page_budgets: [5], default_page_budget: 5, max_depths: [0], default_max_depth: 0, retention_seconds: [null], default_retention_seconds: null },
  assistant: rate,
  sql: { ...rate, max_rows: 1000, max_duration_seconds: 20, max_result_bytes: 8388608 },
}

test("valid access settings retain server-owned SQL limits", () => {
  assert.deepEqual(parseAccessPolicy(policy), policy)
})

test("missing SQL limits cannot reach number formatting", () => {
  assert.throws(() => parseAccessPolicy({ ...policy, sql: rate }), /incomplete or invalid/)
  for (const key of ["max_rows", "max_duration_seconds", "max_result_bytes"]) {
    for (const value of [undefined, null, "1000", 0, -1, NaN, Infinity]) {
      assert.throws(() => parseAccessPolicy({ ...policy, sql: { ...policy.sql, [key]: value } }), /incomplete or invalid/)
    }
  }
  assert.throws(() => parseAccessPolicy({ ...policy, sql: undefined }), /incomplete or invalid/)
})
