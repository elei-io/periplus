import assert from "node:assert/strict"
import test from "node:test"
import type { FrontierSettings } from "../../types/frontier.ts"
import { settingsDraft, settingsPayload } from "./settings-form.ts"

const settings: FrontierSettings = {
  paused: false,
  exclusions: [{ host: "*.example.com", path_prefix: "/private" }],
  collection_limit: 1000,
  interest_limit: 200000,
  acquisition_limit: 10000,
  admission_limit: 10000,
  dispatch_limit: 48,
  captures_per_minute: 60,
  attempt_allowance: 10000,
  capture_time_allowance_ms: 86400000,
  capture_timeout_ms: 120000,
}

test("editing preserves untouched limits, exact milliseconds, and the starting version", () => {
  const state = { policy_version: 7, settings }
  const draft = settingsDraft(state)
  state.policy_version = 8
  assert.deepEqual(settingsPayload(draft), { expected_version: 7, settings })
  draft.settings.exclusions[0].host = "changed.example"
  assert.equal(settings.exclusions[0].host, "*.example.com")
})

test("zero allowances remain zero while unlimited pace uses null", () => {
  const draft = settingsDraft({ policy_version: 2, settings })
  draft.unlimitedRate = true
  const payload = settingsPayload(draft)
  assert.equal(payload.settings.captures_per_minute, null)
})

test("hours and seconds convert without changing other operating allowances", () => {
  const draft = settingsDraft({ policy_version: 1, settings })
  draft.numbers.capture_time_allowance_ms = "1.5"
  draft.numbers.capture_timeout_ms = "45"
  const payload = settingsPayload(draft)
  assert.equal(payload.settings.capture_time_allowance_ms, 5400000)
  assert.equal(payload.settings.capture_timeout_ms, 45000)
})

test("blank or fractional concurrency and zero pace never silently change control meaning", () => {
  for (const value of ["", "1.5", "-1", "10001", "Infinity"]) {
    const draft = settingsDraft({ policy_version: 1, settings })
    draft.numbers.dispatch_limit = value
    assert.throws(() => settingsPayload(draft))
  }
  const draft = settingsDraft({ policy_version: 1, settings })
  draft.rate = "0"
  assert.throws(() => settingsPayload(draft), /Dispatches per minute/)
})
