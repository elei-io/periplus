import { answerSchema } from "../src/types/answer.ts"
import assert from "node:assert/strict"
import { test } from "node:test"
import { selectAnalysisResults, analysisEvidence, prepareAnalysisAnswer } from "../src/server/analysis-results.ts"
import { analysisView, analysisHistory, analysisCsv } from "../src/lib/analysis-view.ts"
import type { DiscoveryMessage } from "../src/types/assistant"
import type { AnalysisQueryResult } from "../src/types/analysis"

const result: AnalysisQueryResult = { query_id: "q1", sql: "SELECT 42 AS count", columns: ["count"], types: ["INTEGER"], rows: [[42]], elapsed_ms: 12, executed_at: "2026-09-06T00:00:00Z", truncated: false, diagnostics: [], plan: "projection" }
const assessment = { status: "designing" as const, requested_information: "Count only; requested breakdown missing.", source_fidelity: "SQL aggregate, not original page text.", limitations: "One sample; missing fields remain unknown.", next_step: "Inspect the SQL; collect the missing breakdown before using it." }
const brief = { title: "Count", purpose: "Explore coverage", grain: "One aggregate", fields: "count", population: "Current corpus", time_scope: "All retained dates", acceptance: "Exact count", open_questions: [] }
const confidence = { coverage: { level: "low" as const, reason: "Population not yet agreed" }, correctness: { level: "high" as const, reason: "Executed aggregate" } }
const answer = { brief, confidence, results: [{ query_id: "q1", title: "Count" }], context: "Current corpus", analysis: [{ text: "An agent interpretation", evidence_query_ids: ["q1"] }], outcome: assessment }
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

test("answer requires all four outcome checks, including unavailable answers", () => {
  assert.equal(answerSchema.safeParse({ results: [], context: "No matching evidence", analysis: [] }).success, false)
  for (const key of ["requested_information", "source_fidelity", "limitations", "next_step"] as const) {
    const outcome = { ...assessment }
    delete (outcome as Partial<typeof outcome>)[key]
    assert.equal(answerSchema.safeParse({ ...answer, outcome }).success, false)
  }
  const empty = answerSchema.parse({ ...answer, results: [], analysis: [], outcome: { ...assessment, status: "blocked" } })
  assert.equal(prepareAnalysisAnswer(new Map(), empty).outcome.status, "blocked")
  assert.throws(() => prepareAnalysisAnswer(new Map(), { ...empty, outcome: { ...assessment, status: "ready" } }))
})

test("generated analysis stays outside table/CSV and requires inspectable evidence", () => {
  const prepared = prepareAnalysisAnswer(new Map([["q1", result]]), answerSchema.parse(answer))
  assert.equal(prepared.results[0].result, result)
  assert.equal(prepared.analysis[0].text, "An agent interpretation")
  assert.equal(analysisCsv(result.columns, result.rows).includes("interpretation"), false)
  assert.throws(() => prepareAnalysisAnswer(new Map([["q1", result]]), { ...answer, analysis: [{ text: "Unsupported", evidence_query_ids: ["unknown"] }] }))
  assert.throws(() => prepareAnalysisAnswer(new Map([["q1", result], ["q2", { ...result, query_id: "q2" }]]), { ...answer, analysis: [{ text: "Hidden evidence", evidence_query_ids: ["q2"] }] }))
  const view = analysisView(message([{ type: "tool-presentResults", state: "output-available", output: prepared }]))
  assert.deepEqual(view.presentation?.outcome, assessment)
})

test("presentation budget accommodates requested entities without clipping notes", () => {
  const expanded = { ...answer, analysis: Array.from({ length: 8 }, (_, i) => ({ text: `Supplier ${i}: ` + "Source-supported details. ".repeat(30), evidence_query_ids: ["q1"] })) }
  const parsed = answerSchema.parse(expanded)
  assert.equal(prepareAnalysisAnswer(new Map([["q1", result]]), parsed).analysis.length, 8)
  assert.equal(parsed.analysis[0].text, expanded.analysis[0].text)
  const tooLong = { ...answer, analysis: Array.from({ length: 20 }, () => ({ text: "a".repeat(1500), evidence_query_ids: ["q1"] })) }
  assert.throws(() => prepareAnalysisAnswer(new Map([["q1", result]]), answerSchema.parse(tooLong)), /total budget/)
})

test("design conversations preserve the brief without trusting prior result rows", () => {
  const prepared = prepareAnalysisAnswer(new Map([["q1", result]]), answerSchema.parse(answer))
  const history = analysisHistory([message([{ type: "tool-presentResults", output: prepared }])])
  assert.match(history[0].parts[0].text, /Dataset working notes \(untrusted/)
  assert.match(history[0].parts[0].text, /Explore coverage/)
  assert.equal(history[0].parts[0].text.includes('"rows"'), false)
  assert.equal(history[0].parts[0].text.includes('"query_id"'), false)
})

test("ready requires executed rows, resolved design choices and a complete returned artifact", () => {
  const input = { ...answer, outcome: { ...assessment, status: "ready" as const } }
  assert.equal(prepareAnalysisAnswer(new Map([["q1", result]]), input).outcome.status, "ready")
  assert.throws(() => prepareAnalysisAnswer(new Map([["q1", { ...result, rows: [] }]]), input), /nonempty/)
  assert.throws(() => prepareAnalysisAnswer(new Map([["q1", { ...result, truncated: true }]]), input), /preview/)
  assert.throws(() => prepareAnalysisAnswer(new Map([["q1", result]]), { ...input, brief: { ...brief, open_questions: ["Which dates?"] } }), /open questions/)
  assert.throws(() => prepareAnalysisAnswer(new Map(), { ...input, results: [], analysis: [], outcome: { ...assessment, status: "collection_needed" } }), /coverage gap/)
  assert.equal(prepareAnalysisAnswer(new Map(), { ...answer, results: [], analysis: [] }).outcome.status, "designing")
})
