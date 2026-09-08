import assert from "node:assert/strict"
import { test } from "node:test"
import { prepareAnalysisAnswer, analysisEvidence, sampleReceipt, approvedBrief } from "../src/server/analysis-results.ts"
import { analysisView, analysisHistory, analysisCsv } from "../src/lib/analysis-view.ts"
import type { AnswerInput } from "../src/types/answer"
import type { AnalysisQueryResult } from "../src/types/analysis"
import type { DiscoveryMessage } from "../src/types/assistant"

const result: AnalysisQueryResult = { schema_version: "public_v1", source_snapshot: 7, query_id: "q1", sql: "SELECT count FROM web.example", columns: ["count"], types: ["INTEGER"], rows: [[42]], elapsed_ms: 12, executed_at: "2026-09-06T00:00:00Z", truncated: false, diagnostics: [], plan: "projection" }
const brief = { title: "Count", grain: "One aggregate", fields: [{ name: "count", type: "INTEGER", meaning: "Number of records", nullable: false }], population: "Current corpus" }
const sample: AnswerInput = { status: "sample", brief, message: "Build this dataset?", limitations: "", needs_sources: false, query_id: "q1", checks: [] }
const check = { ...result, query_id: "check", columns: ["complete"], types: ["BOOLEAN"], rows: [[true]] }
const results = new Map([["q1", result], ["check", check]])
const ready: AnswerInput = { ...sample, status: "ready", checks: ["check"] }

test("samples require actual executed rows and the requested schema", () => {
  assert.equal(prepareAnalysisAnswer(results, sample).dataset?.rows.length, 1)
  assert.throws(() => prepareAnalysisAnswer(new Map(), sample), /successful/)
  assert.throws(() => prepareAnalysisAnswer(new Map([["q1", { ...result, rows: [] }]]), sample), /nonempty/)
  assert.throws(() => prepareAnalysisAnswer(results, { ...sample, brief: { ...brief, fields: [{ ...brief.fields[0], name: "other" }] } }), /columns/)
  assert.throws(() => prepareAnalysisAnswer(results, { ...sample, brief: { ...brief, fields: [{ ...brief.fields[0], type: "VARCHAR" }] } }), /types/)
  const many = { ...result, rows: Array.from({ length: 100 }, (_, i) => [i]) }
  assert.equal(prepareAnalysisAnswer(new Map([["q1", many]]), sample).dataset?.rows.length, 5)
  assert.equal(analysisEvidence(many).rows.length, 20)
  assert.equal("plan" in analysisEvidence(many), false)
})
test("a ready result cannot bypass approval or change the approved scope", () => {
  assert.throws(() => prepareAnalysisAnswer(results, ready), /approval/)
  assert.equal(prepareAnalysisAnswer(results, ready, brief).status, "ready")
  assert.throws(() => prepareAnalysisAnswer(results, { ...ready, brief: { ...brief, population: "Other sources" } }, brief), /exact draft/)
})
test("approval receipts reject forgery, wrong keys and expiry", () => {
  const token = sampleReceipt(brief, "secret")
  assert.deepEqual(approvedBrief(token, "secret"), brief)
  assert.equal(approvedBrief(undefined, "secret"), undefined)
  assert.throws(() => approvedBrief(token, "wrong"))
  assert.throws(() => approvedBrief(token + "x", "secret"))
  assert.throws(() => approvedBrief("forged", "secret"))
  const now = Date.now
  Date.now = () => now() + 86400001
  try { assert.throws(() => approvedBrief(token, "secret"), /expired/) } finally { Date.now = now }
})
test("ready checks non-null output, completeness and actual boolean validation results", () => {
  for (const bad of [{ ...result, rows: [[null]] }, { ...result, truncated: true }]) {
    assert.throws(() => prepareAnalysisAnswer(new Map([["q1", bad], ["check", check]]), ready, brief))
  }
  assert.throws(() => prepareAnalysisAnswer(results, { ...ready, checks: [] }, brief), /passing/)
  assert.throws(() => prepareAnalysisAnswer(results, { ...ready, checks: ["forged"] }, brief), /successful/)
  for (const bad of [{ ...check, rows: [[false]] }, { ...check, rows: [[null]] }, { ...check, rows: [[1]], types: ["INTEGER"] }, { ...check, rows: [] }]) {
    assert.throws(() => prepareAnalysisAnswer(new Map([["q1", result], ["check", bad]]), ready, brief), /passing/)
  }
})
test("clarification needs no query, but missing sources must be inspected", () => {
  const draft = { ...sample, status: "draft" as const, query_id: null }
  assert.equal(prepareAnalysisAnswer(new Map(), draft).dataset, null)
  assert.throws(() => prepareAnalysisAnswer(new Map(), { ...draft, needs_sources: true }), /Inspect/)
})
test("progress survives tools; history preserves only draft and SQL, never approval or result evidence", () => {
  const output = { ...prepareAnalysisAnswer(results, sample), approval: "private-receipt" }
  const message = { id: "m", role: "assistant", parts: [{ type: "text", text: "I found a source." }, { type: "tool-query", toolCallId: "q", state: "output-available", output: { result } }, { type: "tool-updateDataset", toolCallId: "p", state: "output-available", output }] } as DiscoveryMessage
  assert.equal(analysisView(message).finding, "I found a source.")
  assert.equal(analysisView(message).presentation?.status, "sample")
  const history = JSON.stringify(analysisHistory([message]))
  assert.match(history, /untrusted/)
  assert.match(history, /Current corpus/)
  assert.match(history, /SELECT count/)
  assert.equal(history.includes("private-receipt"), false)
  assert.equal(history.includes('query_id'), false)
  assert.equal(history.includes('output-available'), false)
})
test("CSV preserves quoted and multiline cells and precise integers", () => {
  assert.equal(analysisCsv(["name", "count"], [['a,"b"\nc', BigInt("9007199254740993")]]), '"name","count"\r\n"a,""b""\nc","9007199254740993"')
})
