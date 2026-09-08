import type { AnswerInput } from "../types/answer"
import type { AnalysisQueryResult, SelectedResult } from "../types/analysis"

/** Resolve only successful results produced within this agent request. */
export function selectAnalysisResults(results: Map<string, AnalysisQueryResult>, selections: { query_id: string; title: string }[]): SelectedResult[] {
  const seen = new Set<string>()
  return selections.map(({ query_id, title }) => {
    const result = results.get(query_id)
    if (!result || seen.has(query_id)) throw new Error("Select distinct query IDs from successful query results in this request.")
    seen.add(query_id)
    return { title, result }
  })
}

/** Keep execution plans in the UI, outside the model's evidence budget. */
export function analysisEvidence(result: AnalysisQueryResult) {
  return { query_id: result.query_id, sql: result.sql, columns: result.columns, types: result.types,
    rows: result.rows.slice(0, 20), truncated: result.truncated, model_sampled: result.rows.length > 20, source_snapshot: result.source_snapshot, executed_at: result.executed_at,
    elapsed_ms: result.elapsed_ms, diagnostics: result.diagnostics }
}

/** Evidence references must resolve to tables that the user can inspect in this answer. */
export function prepareAnalysisAnswer(results: Map<string, AnalysisQueryResult>, input: AnswerInput) {
  if (JSON.stringify({ analysis: input.analysis, outcome: input.outcome, context: input.context, brief: input.brief, confidence: input.confidence }).length > 16000) {
    throw new Error("Answer prose exceeds its total budget. Shorten complete sentences while preserving requested entities and evidence links.")
  }
  const source_plan = input.source_plan ? { ...input.source_plan, evidence: selectAnalysisResults(results, input.source_plan.query_ids.map(query_id => ({ query_id, title: "Source inspection" }))) } : null
  const selected = selectAnalysisResults(results, input.results)
  const ids = new Set(selected.map(({ result }) => result.query_id))
  for (const note of input.analysis) {
    if (note.evidence_query_ids.some(id => !ids.has(id))) throw new Error("Analysis must reference selected query results.")
  }
  const dataset = selected.find(({ result }) => result.query_id === input.dataset_query_id)?.result
  if (input.dataset_query_id && !dataset) throw new Error("The dataset query must be a selected executed result.")
  const validation = input.validation.map(check => {
    if (check.status !== "untested" && !check.query_ids.length) throw new Error("Tested validation requires executed query evidence.")
    const evidence = selectAnalysisResults(results, check.query_ids.map(query_id => ({ query_id, title: check.check })))
    return { ...check, evidence }
  })
  if (input.outcome.status === "ready") {
    if (!source_plan) throw new Error("Inspect available source material before delivering a ready dataset.")
    if (!dataset) throw new Error("A ready dataset requires a dataset query, separate from coverage evidence.")
    const names = input.brief.fields.map(field => field.name)
    if (new Set(names).size !== names.length || JSON.stringify(names) !== JSON.stringify(dataset.columns)) throw new Error("Dataset columns must match the agreed schema in order, with unique names.")
    if (input.brief.fields.some((field, index) => field.type.toUpperCase() !== dataset.types[index]?.toUpperCase())) throw new Error("Dataset types must match the agreed schema.")
    if (dataset.rows.some(row => input.brief.fields.some((field, index) => !field.nullable && row[index] == null))) throw new Error("Dataset contains missing values in required fields.")
    if (!validation.length || validation.some(check => check.status !== "passed")) throw new Error("Ready requires passing validation; unresolved checks remain designing.")
    if (!dataset.rows.length) throw new Error("A ready dataset requires executed, nonempty query results.")
    if (input.brief.open_questions.length) throw new Error("Resolve the dataset brief's open questions before marking it ready.")
    if (selected.some(({ result }) => result.truncated)) throw new Error("A truncated result is incomplete. Continue designing or narrow the agreed dataset before marking it ready.")
  }
  if (input.outcome.status === "collection_needed" && !selected.length) throw new Error("A collection recommendation requires query evidence of the coverage gap.")
  return { ...input, results: selected, validation, source_plan }
}
