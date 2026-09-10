import assert from "node:assert/strict"
import { test } from "node:test"
import { suggestDataset, analysisEvidence } from "../src/server/analysis-results.ts"
import { analysisView, analysisHistory, analysisCsv, inspectedEvidence } from "../src/lib/analysis-view.ts"
import { datasetDraftSchema, datasetRequestSchema, sameDatasetBrief, type DatasetSuggestion } from "../src/types/answer.ts"
import type { AnalysisQueryResult } from "../src/types/analysis"
import type { DiscoveryMessage } from "../src/types/assistant"

const result: AnalysisQueryResult = { schema_version: "public_v1", source_snapshot: 7, query_id: "q1", sql: "SELECT count FROM web.example", columns: ["count"], types: ["INTEGER"], rows: [[42]], elapsed_ms: 12, executed_at: "2026-09-06T00:00:00Z", truncated: false, diagnostics: [], plan: "projection" }
const brief = { title: "Count", grain: "One aggregate", fields: [{ name: "count", type: "INTEGER", meaning: "Number of records", nullable: false }], population: "Current corpus" }
const sample: DatasetSuggestion = { title: brief.title, grain: brief.grain, population: brief.population, limitations: "", query_id: "q1", checks: [] }
const check = { ...result, query_id: "check", columns: ["complete"], types: ["BOOLEAN"], rows: [[true]] }
const results = new Map([["q1", result], ["check", check]])
const ready = { ...sample, checks: ["check"] }

test("suggestions require executed data but no schema prerequisite", () => {
  assert.throws(() => suggestDataset(new Map(), sample), /successful/)
  assert.equal(suggestDataset(results, sample).brief.fields[0].nullable, true)
  const many = { ...result, rows: Array.from({ length: 100 }, (_, i) => [i]) }
  assert.equal(suggestDataset(new Map([["q1", many]]), sample).dataset.rows.length, 5)
  assert.equal(analysisEvidence(many).rows.length, 20)
})
test("only an explicit validated contract can be ready", () => {
  assert.equal(suggestDataset(results, ready).status, "sample")
  assert.equal(suggestDataset(results, ready, undefined, brief).status, "sample")
  assert.equal(suggestDataset(results, ready, brief).status, "ready")
  assert.equal(suggestDataset(results, { ...ready, population: "Current corpus." }, brief).brief.population, brief.population)
  assert.throws(() => suggestDataset(results, { ...ready, checks: ["forged"] }, brief), /successful/)
})
test("contract failures preserve previews with issues", () => {
  for (const bad of [{ ...result, rows: [[null]] }, { ...result, truncated: true }, { ...result, types: ["VARCHAR"] }, { ...result, columns: ["other"] }]) {
    const output = suggestDataset(new Map([["q1", bad], ["check", check]]), ready, brief)
    assert.equal(output.status, "sample")
    assert.ok(output.issues.length)
  }
  for (const bad of [{ ...check, rows: [[false]] }, { ...check, rows: [] }, { ...check, rows: [[1]], types: ["INTEGER"] }, { ...check, rows: [[true, true]] }]) {
    assert.equal(suggestDataset(new Map([["q1", result], ["check", bad]]), ready, brief).status, "sample")
  }
  assert.equal(suggestDataset(results, sample, brief).status, "sample")
  assert.equal(suggestDataset(new Map([["q1", { ...result, rows: [] }]]), sample).status, "draft")
})
test("independent suggestions preserve final prose and ignore failed suggestions", () => {
  const message = { id: "m", role: "assistant", parts: [
    { type: "tool-SUGGEST_SCHEMA", toolCallId: "s", state: "output-available", output: { brief } },
    { type: "tool-SUGGEST_DATASET", toolCallId: "d", state: "output-available", output: suggestDataset(results, sample) },
    { type: "tool-SUGGEST_DATASET", toolCallId: "e", state: "output-available", output: { error: "Failed" } },
    { type: "tool-SUGGEST_COVERAGE_REQUEST", toolCallId: "c", state: "output-available", output: { description: "More listings", reason: "Expand coverage" } },
    { type: "text", text: "Here is the sample." }
  ] } as DiscoveryMessage
  const view = analysisView(message)
  assert.equal(view.finding, "Here is the sample.")
  assert.equal(view.presentation?.status, "sample")
  assert.deepEqual(view.schema, brief)
  assert.equal(view.coverage.length, 1)
  const history = JSON.stringify(analysisHistory([message]))
  assert.match(history, /untrusted/)
  assert.match(history, /SELECT count/)
  assert.equal(history.includes("query_id"), false)
})
test("CSV preserves quoted and multiline cells and precise integers", () => {
  assert.equal(analysisCsv(["name", "count"], [['a,"b"\nc', BigInt("9007199254740993")]]), '"name","count"\r\n"a,""b""\nc","9007199254740993"')
})

test("workspace requests keep explicit contracts separate from exploration", () => {
  assert.equal(datasetRequestSchema.safeParse({ mode: "discover" }).success, true)
  assert.equal(datasetRequestSchema.safeParse({ mode: "discover", contract: brief }).success, false)
  assert.equal(datasetRequestSchema.safeParse({ mode: "build", contract: brief }).success, true)
  assert.equal(datasetRequestSchema.safeParse({ mode: "build", contract: { ...brief, fields: [...brief.fields, ...brief.fields] } }).success, false)
  assert.equal(datasetRequestSchema.safeParse({ mode: "build", contract: { ...brief, fields: [{ ...brief.fields[0], name: "Listing ID" }] } }).success, true)
})
test("contract comparison ignores object key order but preserves field order and nullability", () => {
  assert.equal(sameDatasetBrief(brief, { population: brief.population, fields: [{ nullable: false, meaning: "Number of records", type: "INTEGER", name: "count" }], grain: brief.grain, title: brief.title }), true)
  assert.equal(sameDatasetBrief(brief, { ...brief, fields: [{ ...brief.fields[0], nullable: true }] }), false)
})

test("follow-ups retain attempted extraction SQL even when the final query failed", () => {
  const message = { id: "failed", role: "assistant", parts: [{ type: "tool-SQL", input: { sql: "SELECT price FROM candidate_listings" }, output: { error: "Connection lost" } }] }
  const history = JSON.stringify(analysisHistory([message]))
  assert.match(history, /SELECT price FROM candidate_listings/)
  assert.match(history, /may have failed/)
  assert.equal(history.includes('"output"'), false)
})

test("validation identifies exact field discrepancies", () => {
  const changed = { ...result, types: ["VARCHAR"] }
  const output = suggestDataset(new Map([["q1", changed], ["check", check]]), { ...ready, grain: "Other grain", population: "Other scope" }, brief)
  assert.ok(output.issues.some(issue => issue.includes("Column count: expected INTEGER; received VARCHAR")))
})
test("suggestion wording cannot change or invalidate the required contract", () => {
  const output = suggestDataset(results, { ...ready, title: "A friendlier name", grain: "One aggregate.", population: "Current corpus." }, brief)
  assert.equal(output.status, "ready")
  assert.deepEqual(output.brief, brief)
})
test("successful inspection remains accessible after a later failed query without becoming a dataset", () => {
  const message = { id: "partial", role: "assistant", parts: [
    { type: "tool-SQL", toolCallId: "one", state: "output-available", input: { purpose: "Find evidence", sql: result.sql }, output: { result } },
    { type: "tool-SQL", toolCallId: "two", state: "output-available", input: { purpose: "Dig deeper", sql: "SELECT more" }, output: { error: "Timeout" } },
  ] } as DiscoveryMessage
  assert.equal(analysisView(message).queries.length, 2)
  assert.equal(analysisView(message).presentation, undefined)
  assert.equal(analysisView(message).schema, undefined)
})

test("unfinished drafts round-trip without being accepted as valid build contracts", () => {
  const draft = { title: "Work in progress", grain: "", population: "", fields: [{ name: "", type: "", meaning: "", nullable: true }] }
  assert.deepEqual(datasetDraftSchema.parse(JSON.parse(JSON.stringify(draft))), draft)
  assert.equal(datasetRequestSchema.safeParse({ mode: "build", contract: draft }).success, false)
  assert.equal(datasetDraftSchema.safeParse({ type: "object", properties: {} }).success, false)
})

test("Findings keeps successful evidence across failed and unfinished turns", () => {
  const messages = [
    { id: "first", role: "assistant", parts: [{ type: "tool-SQL", state: "output-available", toolCallId: "q", input: { sql: result.sql, purpose: "Inspect sources" }, output: { result } }] },
    { id: "second", role: "assistant", parts: [{ type: "tool-SQL", state: "output-available", toolCallId: "failed", output: { error: "Service busy" } }] },
    { id: "third", role: "assistant", parts: [{ type: "tool-SQL", state: "input-available", toolCallId: "pending", input: { sql: "SELECT more", purpose: "More" } }] },
  ] as DiscoveryMessage[]
  assert.deepEqual(inspectedEvidence(messages), [{ result, purpose: "Inspect sources" }])
  assert.equal(analysisView(messages[0]).presentation, undefined)
})
