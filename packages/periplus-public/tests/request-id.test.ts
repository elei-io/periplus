import assert from "node:assert/strict"
import { test } from "node:test"
import { createRequestId } from "../src/lib/request-id.ts"

test("request IDs remain valid and distinct without secure-context randomUUID", t => {
  const getRandomValues = globalThis.crypto.getRandomValues.bind(globalThis.crypto)
  t.mock.getter(globalThis, "crypto", () => ({ getRandomValues }) as Crypto)
  assert.equal(typeof crypto.randomUUID, "undefined")
  const ids = Array.from({ length: 100 }, () => createRequestId())
  for (const id of ids) {
    assert.match(id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
  }
  assert.equal(new Set(ids).size, ids.length)
})
