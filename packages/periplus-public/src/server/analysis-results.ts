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
    rows: result.rows, truncated: result.truncated, executed_at: result.executed_at,
    elapsed_ms: result.elapsed_ms, diagnostics: result.diagnostics }
}
