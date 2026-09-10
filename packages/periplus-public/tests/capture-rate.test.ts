import test from "node:test"
import assert from "node:assert/strict"
import { CaptureRate } from "../src/lib/capture-rate.ts"
import type { CapturePage } from "../src/types/live.ts"
const page = (ids: string[], extra: Partial<CapturePage> = {}): CapturePage => ({
  items: ids.map(observation_id => ({observation_id, requested_url: "https://example.com", completed_at: "2026-09-10T00:00:00Z", evidence_committed: false, query_ready: null, query_readiness_reason: ""})),
  cursor: "cursor", has_more: false, bootstrap: false, reset_reason: null, as_of: "2026-09-10T00:00:00Z", source: "retained_public_completions", ...extra,
})
test("excludes initial history and deduplicates arrivals before averaging", () => {
  const rate = new CaptureRate()
  assert.equal(rate.accept(page(["old"], {bootstrap: true}), 0), undefined)
  assert.equal(rate.accept(page(["old", "a", "a", "b"]), 5000), 24)
  assert.equal(rate.accept(page(["b"]), 10000), 12)
  for (let now = 15000; now <= 65000; now += 5000) rate.accept(page([]), now)
  assert.equal(rate.accept(page([]), 70000), 0)
})
test("resets after pauses, cursor resets, and paginated catch-up", () => {
  const rate = new CaptureRate()
  rate.accept(page([]), 0)
  assert.equal(rate.accept(page(["a"]), 5000), 12)
  assert.equal(rate.accept(page(["b"]), 30000), undefined)
  assert.equal(rate.accept(page(["c"], {has_more: true}), 35000), undefined)
  assert.equal(rate.accept(page(["d"]), 35100), undefined)
  assert.equal(rate.accept(page(["e"]), 40100), 12)
  assert.equal(rate.accept(page(["f"], {reset_reason: "cursor_expired"}), 45100), undefined)
})
