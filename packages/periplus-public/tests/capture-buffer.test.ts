import test from "node:test"
import assert from "node:assert/strict"
import { CaptureBuffer } from "../src/lib/capture-buffer.ts"
import type { RecentCapture } from "../src/types/live.ts"
const captures = (start: number, count: number): RecentCapture[] => Array.from({length:count}, (_, i) => ({observation_id:String(start+i), requested_url:`https://example.com/${start+i}`, completed_at:"2026-09-07T00:00:00Z", evidence_committed:false, query_ready:null, query_readiness_reason:"not_verified"}))

test("only new observations enter the animation queue, including duplicate retries", () => {
  const buffer = new CaptureBuffer()
  buffer.accept(captures(0,7), 0)
  assert.equal(buffer.snapshot().visible[0].observation_id, "6")
  assert.equal(buffer.accept(captures(0,7), 0).pending, 0)
  buffer.accept([...captures(7,3), ...captures(7,3)], 0)
  assert.equal(buffer.snapshot().pending, 3)
  assert.equal(buffer.tick(100), null)
  assert.equal(buffer.tick(300)?.pending, 2)
  assert.equal(buffer.snapshot().visible[0].observation_id, "7")
})

test("large bursts drain within three seconds with bounded display and storage", () => {
  for (const size of [1, 3, 7, 200, 600, 2000]) {
    const buffer = new CaptureBuffer()
    buffer.accept([], 0)
    buffer.accept(captures(0, size), 0)
    assert.ok(buffer.snapshot().pending <= 1000)
    for (let time = 100; time <= 3000; time += 100) buffer.tick(time)
    assert.equal(buffer.snapshot().pending, 0)
    assert.equal(buffer.snapshot().burst, size)
    assert.equal(buffer.snapshot().visible[0].observation_id, String(size-1))
    assert.ok(buffer.snapshot().visible.length <= 7)
    assert.equal(buffer.tick(10000), null)
  }
})

test("reduced motion drains immediately and a resync replaces the old view", () => {
  const buffer = new CaptureBuffer()
  buffer.accept(captures(0,7), 0)
  buffer.accept(captures(7,10), 0)
  assert.equal(buffer.tick(0, true)?.pending, 0)
  assert.equal(buffer.snapshot().visible[0].observation_id, "16")
  buffer.accept(captures(100,7), 500, true)
  assert.equal(buffer.snapshot().visible[0].observation_id, "106")
  assert.equal(buffer.snapshot().burst, 0)
})
