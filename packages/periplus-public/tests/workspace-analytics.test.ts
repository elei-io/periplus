import assert from "node:assert/strict"
import test from "node:test"
import { workspaceOutput } from "../src/lib/workspace-analytics.ts"
import { navigationAnalytics } from "../src/lib/navigation-analytics.ts"
import type { DiscoveryMessage } from "../src/types/assistant"

test("an answer without a dataset is observable output, not a blocked operation", () => {
  const output = workspaceOutput({ id: "answer", role: "assistant", parts: [{ type: "text", text: "These sources cannot answer the question." }] } as DiscoveryMessage)
  assert.equal(output.has_answer, true)
  assert.equal(output.has_dataset, false)
  assert.equal(output.dataset_status, "none")
  assert.equal("outcome" in output, false)
})
test("empty successful evidence differs from failed SQL", () => {
  const output = workspaceOutput({ id: "evidence", role: "assistant", parts: [
    { type: "tool-SQL", state: "output-available", output: { result: { rows: [] } } },
    { type: "tool-SQL", state: "output-available", output: { error: "Unavailable" } },
  ] } as unknown as DiscoveryMessage)
  assert.equal(output.has_evidence, true)
  assert.equal(output.has_nonempty_evidence, false)
  assert.equal(output.sql_error_count, 1)
})
test("navigation retains known routes and context flags only", () => {
  const event = navigationAnalytics('/build?question=secret&draft=private', 'https://periplus.elei.io/sql?sql=private')
  assert.equal(event?.event, "workspace_handoff_opened")
  assert.equal(event?.properties.from, '/sql')
  assert.equal(event?.properties.to, '/build')
  assert.equal(event?.properties.has_schema, true)
  assert.doesNotMatch(JSON.stringify(event), /secret|private/)
  assert.equal(navigationAnalytics('https://other.example/build', 'https://periplus.elei.io/'), null)
  assert.equal(navigationAnalytics('/unknown', 'https://periplus.elei.io/'), null)
})
