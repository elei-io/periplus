import assert from "node:assert/strict"
import { test } from "node:test"
import { collectionSubmission } from "./submission.ts"

function form() {
  const value = new FormData()
  for (const [key, text] of Object.entries({
    seed_urls: "https://example.com/a?x=1\nhttps://example.com/a?x=2",
    follow_sql: "SELECT target_url AS url FROM nav.links",
    max_depth: "0",
    page_limit: "25",
    result_max_age_seconds: "0",
    request_class: "admin",
  }))
    value.set(key, text)
  return value
}
test("submission preserves conservative URLs, zero depth and fresh-result intent", () => {
  const spec = collectionSubmission(form())
  assert.deepEqual(spec.seed_urls, [
    "https://example.com/a?x=1",
    "https://example.com/a?x=2",
  ])
  assert.equal(spec.max_depth, 0)
  assert.equal(spec.result_max_age_seconds, 0)
  assert.equal(spec.request_class, "admin")
})
test("SQL parameters and system class survive submission", () => {
  const value = form()
  value.set(
    "seed_sql",
    "SELECT requested_url AS url FROM web.observation WHERE requested_url = ? LIMIT 10"
  )
  value.set("seed_parameters", '["example.com"]')
  value.set("request_class", "system")
  value.set("max_duration_seconds", "3600")
  const spec = collectionSubmission(value)
  assert.deepEqual(spec.seed_parameters, ["example.com"])
  assert.equal(spec.request_class, "system")
  assert.equal(spec.max_duration_seconds, 3600)
})
test("missing intent, orphan parameters, and invalid budgets do not silently coerce", () => {
  const value = form()
  value.set("page_limit", "")
  assert.throws(() => collectionSubmission(value), /integer/)
  value.set("page_limit", "1.5")
  assert.throws(() => collectionSubmission(value), /integer/)
  value.set("page_limit", "25")
  value.set("seed_parameters", '["orphan"]')
  assert.throws(() => collectionSubmission(value), /require seed SQL/)
  value.set("seed_parameters", "[]")
  value.set("seed_urls", "")
  assert.throws(() => collectionSubmission(value), /Provide starting/)
})
