import assert from "node:assert/strict"
import test from "node:test"
import type { FrontierSettings } from "../../types/frontier.ts"
import { settingsDraft, settingsPayload } from "./settings-form.ts"

const settings: FrontierSettings = {
  paused: false,
  exclusions: [{ host: "*.example.com", path_prefix: "/private" }],
  dispatch_limit: 48,
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

test("seconds convert to the per-capture timeout", () => {
  const draft = settingsDraft({ policy_version: 1, settings })
  draft.numbers.capture_timeout_ms = "45"
  const payload = settingsPayload(draft)
  assert.equal(payload.settings.capture_timeout_ms, 45000)
})

test("blank or fractional concurrency never silently change control meaning", () => {
  for (const value of ["", "1.5", "-1", "10001", "Infinity"]) {
    const draft = settingsDraft({ policy_version: 1, settings })
    draft.numbers.dispatch_limit = value
    assert.throws(() => settingsPayload(draft))
  }
})
