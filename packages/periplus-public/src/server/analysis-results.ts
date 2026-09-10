import { type DatasetSuggestion, type DatasetBrief } from "../types/answer.ts"
import type { AnalysisQueryResult } from "../types/analysis"

/** Only server-executed results are evidence. Plans stay outside the model budget. */
export function analysisEvidence(result: AnalysisQueryResult) {
  return { query_id: result.query_id, sql: result.sql, columns: result.columns, types: result.types,
    rows: result.rows.slice(0, 20), truncated: result.truncated, model_sampled: result.rows.length > 20,
    source_snapshot: result.source_snapshot }
}

export function suggestDataset(results: Map<string, AnalysisQueryResult>, input: DatasetSuggestion, contract?: DatasetBrief, proposal?: DatasetBrief) {
  const result = results.get(input.query_id)
  if (!result) throw new Error("Select a successful SQL query from this turn.")
  const checks = input.checks.map(id => {
    const check = results.get(id)
    if (!check) throw new Error("Validation requires successful SQL queries from this turn.")
    return check
  })
  const expected = contract ?? proposal
  const brief: DatasetBrief = {
    title: contract?.title ?? input.title, grain: contract?.grain ?? input.grain, population: contract?.population ?? input.population,
    fields: result.columns.map((name, index) => {
      const field = expected?.fields.find(field => field.name === name)
      return { name, type: result.types[index], meaning: field?.meaning ?? "", nullable: field?.nullable ?? true }
    }),
  }
  const issues: string[] = []
  if (contract) {
    if (JSON.stringify(result.columns) !== JSON.stringify(contract.fields.map(field => field.name))) issues.push(`Column names/order: expected ${contract.fields.map(field => field.name).join(", ")}; received ${result.columns.join(", ")}.`)
    for (const field of contract.fields) {
      const index = result.columns.indexOf(field.name)
      if (index >= 0 && result.types[index] !== field.type) issues.push(`Column ${field.name}: expected ${field.type}; received ${result.types[index]}.`)
    }
    if (!result.rows.length) issues.push("The query returned no rows.")
    if (result.truncated) issues.push("The query result is truncated.")
    for (const field of contract.fields) {
      const index = result.columns.indexOf(field.name)
      if (!field.nullable && index >= 0 && result.rows.some(row => row[index] == null)) issues.push(`Required column ${field.name} contains missing values.`)
    }
    if (!checks.length) issues.push("No executed checks for row grain, source scope and transformations were supplied.")
    else if (checks.some(check => check.truncated || check.rows.length !== 1 || !check.columns.length || check.types.length !== check.columns.length || check.rows[0].length !== check.columns.length || check.types.some(type => type !== "BOOLEAN") || check.rows[0].some(value => value !== true))) issues.push("One or more validation checks failed or did not return a single row of boolean results.")
  }
  const status: "ready" | "sample" | "draft" = contract && !issues.length ? "ready" : result.rows.length ? "sample" : "draft"
  return { brief, status, limitations: input.limitations, issues, checks,
    dataset: { ...result, rows: status === "ready" ? result.rows : result.rows.slice(0, 5) } }
}
