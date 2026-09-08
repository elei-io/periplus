import assert from "node:assert/strict"
import test from "node:test"
import { accountedBytes, bytes, perObservation } from "./storage-format.ts"

const source = (value: number | null) => ({ id: "test", name: "Test", bytes: value, complete: value !== null, basis: "test", reason: null })

test("unknown storage is not zero and partial totals include only measured sources", () => {
  assert.equal(bytes(null), "—")
  assert.equal(bytes(0), "0 B")
  assert.equal(accountedBytes([source(null)]), null)
  assert.equal(accountedBytes([source(0), source(null)]), 0)
  assert.equal(accountedBytes([source(100), source(null), source(50)]), 150)
})

test("amortized storage does not divide by zero or count repeated references", () => {
  assert.equal(perObservation(100, 0), null)
  assert.equal(perObservation(100, 2), 50)
  assert.equal(bytes(1024), "1 KiB")
  assert.equal(bytes(perObservation(1, 10)), "<1 B")
})
