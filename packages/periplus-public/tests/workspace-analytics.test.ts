import assert from "node:assert/strict"
import test from "node:test"
import { navigationAnalytics } from "../src/lib/navigation-analytics.ts"
test("navigation retains known routes and context flags only", () => {
  const event = navigationAnalytics('/sql?sql=secret', 'https://periplus.elei.io/sql?sql=private')
  assert.equal(event?.event, "workspace_handoff_opened")
  assert.equal(event?.properties.from, '/sql')
  assert.equal(event?.properties.to, '/sql')
  assert.equal(event?.properties.has_sql, true)
  assert.doesNotMatch(JSON.stringify(event), /secret|private/)
  assert.equal(navigationAnalytics('https://other.example/build', 'https://periplus.elei.io/'), null)
  assert.equal(navigationAnalytics('/unknown', 'https://periplus.elei.io/'), null)
})
