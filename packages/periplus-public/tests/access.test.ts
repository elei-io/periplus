import test from "node:test"
import assert from "node:assert/strict"
import { parseAccessPolicy } from "../src/types/access.ts"

const rate = { enabled: true, requests: 60, window_seconds: 60 }
const policy = {
  version: 3,
  crawl_admission: { pending_acquisitions: 42, accepting: true },
  crawl: { ...rate, queue_limit: 10000, page_budgets: [5], default_page_budget: 5, follow_link_limits: [1000, 5000], default_follow_link_limit: 1000, max_depths: [0], default_max_depth: 0, retention_seconds: [null], default_retention_seconds: null },
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

 test("public admission requires a valid queue threshold and live status", () => {
  assert.equal(parseAccessPolicy({ ...policy, crawl: { ...policy.crawl, queue_limit: null } }).crawl.queue_limit, null)
  for (const queue_limit of [0, -1, 1.5, undefined])
    assert.throws(() => parseAccessPolicy({ ...policy, crawl: { ...policy.crawl, queue_limit } }), /incomplete or invalid/)
  assert.throws(() => parseAccessPolicy({ ...policy, crawl_admission: undefined }), /incomplete or invalid/)
})

test("missing follow-link choices fail closed", () => {
  assert.throws(() => parseAccessPolicy({ ...policy, crawl: { ...policy.crawl, follow_link_limits: undefined } }), /incomplete or invalid/)
})
