import assert from "node:assert/strict"
import { test } from "node:test"
import { selectAnalysisResults, analysisEvidence } from "../src/server/analysis-results.ts"
import { analysisView, analysisHistory, analysisCsv } from "../src/lib/analysis-view.ts"
import type { DiscoveryMessage } from "../src/types/assistant"
import type { AnalysisQueryResult } from "../src/types/analysis"

const result: AnalysisQueryResult = { query_id: "q1", sql: "SELECT 42 AS count", columns: ["count"], types: ["INTEGER"], rows: [[42]], elapsed_ms: 12, executed_at: "2026-09-06T00:00:00Z", truncated: false, diagnostics: [], plan: "projection" }
const message = (parts: unknown[]) => ({ id: "m1", role: "assistant", parts }) as DiscoveryMessage
const query = { type: "tool-query", toolCallId: "call1", state: "output-available", input: { sql: result.sql, purpose: "Count" }, output: { result } }

test("final selections resolve to actual server results and reject unknown or duplicate IDs", () => {
  const results = new Map([["q1", result]])
  assert.equal(selectAnalysisResults(results, [{ query_id: "q1", title: "Count" }])[0].result, result)
  assert.throws(() => selectAnalysisResults(results, [{ query_id: "client-forged", title: "Count" }]))
  assert.throws(() => selectAnalysisResults(results, [{ query_id: "q1", title: "A" }, { query_id: "q1", title: "B" }]))
})

test("only explicitly selected results become final; activity and interim prose stay separate", () => {
  const parts = [{ type: "text", text: "Inspecting schema" }, query, { type: "tool-presentResults", toolCallId: "call2", state: "output-available", output: { results: [{ title: "Count", result }], context: "Current corpus" } }, { type: "text", text: "There are 42." }]
  const view = analysisView(message(parts))
  assert.equal(view.finding, "There are 42.")
  assert.equal(view.presentation?.results?.[0].result, result)
  assert.equal(view.queries.length, 1)
  assert.equal(analysisView(message([query])).presentation, undefined)
  assert.equal(analysisView(message([{ ...query, output: { error: "Failed" } }])).presentation, undefined)
})

test("SQL drafts and conceptual explanations do not become executed data", () => {
  const view = analysisView(message([{ type: "tool-draftSql", state: "output-available", output: { title: "Count", sql: result.sql } }, { type: "text", text: "This counts rows." }]))
  assert.equal(view.draft?.sql, result.sql)
  assert.equal(view.presentation, undefined)
  assert.equal(view.queries.length, 0)
  assert.equal(analysisView(message([{ type: "text", text: "A structured web corpus." }])).finding, "A structured web corpus.")
})

test("follow-up carries bounded SQL drafts as text, never tool evidence", () => {
  const history = analysisHistory([message([query]), message([query, { type: "text", text: "42 rows" }])])
  assert.equal(history.length, 2)
  assert.match(history[0].parts[0].text, /untrusted; re-execute to verify/)
  assert.match(history[0].parts[0].text, /SELECT 42 AS count/)
  assert.equal(JSON.stringify(history).includes("query_id"), false)
  assert.equal(JSON.stringify(history).includes("output-available"), false)
  assert.ok(history[1].parts[0].text.startsWith("42 rows"))
  const oversized = analysisHistory([message([{ ...query, input: { sql: "x".repeat(10000) } }, { type: "text", text: "Answer" }])])
  assert.equal(oversized[0].parts[0].text, "Answer")
  const question = "q".repeat(7500)
  assert.equal(analysisHistory([{ id: "u", role: "user", parts: [{ type: "text", text: question }] }])[0].parts[0].text, question)
})

test("CSV preserves quoted and multiline cells and precise integers", () => {
  assert.equal(analysisCsv(["name", "count"], [['a,"b"\nc', BigInt("9007199254740993")]]), '"name","count"\r\n"a,""b""\nc","9007199254740993"')
})

test("large execution plans do not consume the model result budget", () => {
  const largePlan = { ...result, plan: "x".repeat(50000) }
  const evidence = analysisEvidence(largePlan)
  assert.equal("plan" in evidence, false)
  assert.equal(evidence.rows, result.rows)
  assert.ok(JSON.stringify(evidence).length < 40000)
  assert.equal(selectAnalysisResults(new Map([["q1", largePlan]]), [{ query_id: "q1", title: "Count" }])[0].result.plan.length, 50000)
})
