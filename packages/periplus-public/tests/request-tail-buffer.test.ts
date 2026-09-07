import test from "node:test"
import assert from "node:assert/strict"
import { RequestTailBuffer } from "../src/lib/request-tail-buffer.ts"
import type { CollectionArrivalsPage } from "../src/types/frontier-items.ts"

type Arrival = CollectionArrivalsPage["items"][number]
const arrival = (id: number): Arrival => ({fulfillment_id:`f${id}`,observation_id:String(id),requested_url:`https://example.com/${id}`,parent_observation_id:null,depth:0,rule_id:"seed",mode:"acquired",decided_at:"2026-09-07T00:00:00Z",observation_committed:false,effective_url:null,observed_at:null,outcome:null,http_status_code:null,query_ready:null,query_readiness_reason:"pending"})
const ids = (items: Arrival[] | null) => items?.map(item => item.observation_id)

test("a poll burst inserts one observation at a time, pushing older rows down", () => {
  const buffer = new RequestTailBuffer()
  assert.deepEqual(ids(buffer.accept([5,4,3,2,1].map(arrival))), ["5","4","3","2","1"])
  buffer.accept([8,7,6,5,4,3].map(arrival))
  assert.deepEqual(ids(buffer.advance()), ["6","5","4","3","2"])
  // An overlapping poll must not replay either pending or visible observations.
  buffer.accept([8,7,6,5,4,3].map(arrival))
  assert.deepEqual(ids(buffer.advance()), ["7","6","5","4","3"])
  assert.deepEqual(ids(buffer.advance()), ["8","7","6","5","4"])
  assert.equal(buffer.advance(), null)
})

test("late metadata refreshes visible and queued rows without another arrival", () => {
  const buffer = new RequestTailBuffer()
  buffer.accept([arrival(1)])
  buffer.accept([arrival(2),arrival(1)])
  const ready = (id: number) => ({...arrival(id),outcome:"succeeded",observation_committed:true})
  assert.equal(buffer.accept([ready(2),ready(1)])[0].outcome, "succeeded")
  assert.equal(buffer.advance()?.[0].outcome, "succeeded")
  buffer.accept([ready(2),ready(1)])
  assert.equal(buffer.advance(), null)
})

test("reduced motion catches up immediately; a reopened request starts with its own snapshot", () => {
  const buffer = new RequestTailBuffer()
  buffer.accept([])
  buffer.accept([3,2,1].map(arrival))
  assert.deepEqual(ids(buffer.advance(true)), ["3","2","1"])
  assert.equal(buffer.advance(), null)
  const other = new RequestTailBuffer()
  assert.deepEqual(ids(other.accept([arrival(9)])), ["9"])
})
