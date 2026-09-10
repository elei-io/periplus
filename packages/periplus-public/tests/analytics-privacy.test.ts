import test from "node:test"
import assert from "node:assert/strict"
import { analyticsUrl, redactAnalyticsProperties } from "../src/lib/analytics-privacy.ts"

test("analytics URLs never contain credentials, SQL, prompts or fragments", () => {
  assert.equal(analyticsUrl("https://user:password@periplus.dev/sql?sql=secret&parameters=private#fragment"), "https://periplus.dev/sql")
  assert.equal(analyticsUrl("data:text/plain,secret"), "[redacted]")
  assert.equal(analyticsUrl("secret"), "[redacted]")
})
test("nested attribution and exception payloads are redacted without losing bounded metrics", () => {
  const properties = { $current_url: "https://periplus.dev/discover?question=secret", $set_once: { $initial_referrer: "https://example.com/?token=secret" }, $exception_list: [{ type: "Error", value: "SQL contains secret", stacktrace: { frames: [{ filename: "https://periplus.dev/chunk.js?secret" }] } }], row_count: 42, outcome: "success" }
  const redacted = redactAnalyticsProperties(properties)
  assert.doesNotMatch(JSON.stringify(redacted), /secret/)
  assert.equal((redacted as typeof properties).row_count, 42)
  assert.equal((redacted as typeof properties).outcome, "success")
  assert.match(properties.$current_url, /secret/) // Does not mutate reusable source data.
})

test("structured content and exception source context cannot escape redaction", () => {
  const properties = { parameters: [123, { nested: "private" }], rows: [[123, "private"]], $exception_list: [{ stacktrace: { frames: [{ context_line: "private", pre_context: ["private"] }] } }] };
  const redacted = JSON.stringify(redactAnalyticsProperties(properties));
  assert.doesNotMatch(redacted, /private|123/);
})
