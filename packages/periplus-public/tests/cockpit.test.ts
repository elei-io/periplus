import test from "node:test"
import assert from "node:assert/strict"
import { speedometerScale } from "../src/lib/cockpit.ts"

test("speedometer scales are labeled, useful at low rates, and contain the reading", () => {
  assert.equal(speedometerScale(2), 5)
  assert.equal(speedometerScale(0), 5)
  assert.equal(speedometerScale(undefined), 5)
  assert.equal(speedometerScale(48), 100)
  for(const rate of [0.1, 2, 9, 48, 110, 1500]) assert.ok(speedometerScale(rate) > rate)
})
