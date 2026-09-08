import { createHmac, timingSafeEqual } from "node:crypto"
import { datasetBriefSchema, type AnswerInput, type DatasetBrief } from "../types/answer.ts"
import type { AnalysisQueryResult } from "../types/analysis"

/** Only server-executed results are evidence. Plans stay outside the model budget. */
export function analysisEvidence(result: AnalysisQueryResult) {
  return { query_id: result.query_id, sql: result.sql, columns: result.columns, types: result.types,
    rows: result.rows.slice(0, 20), truncated: result.truncated, model_sampled: result.rows.length > 20,
    source_snapshot: result.source_snapshot }
}
function signature(payload: string, secret: string) {
  return createHmac("sha256", secret).update("dataset-sample:" + payload).digest("base64url")
}
/** Stateless approval receipt: no persistence or trust in client tool evidence. */
export function sampleReceipt(brief: DatasetBrief, secret: string) {
  const payload = Buffer.from(JSON.stringify({ brief, expires: Date.now() + 86400000 })).toString("base64url")
  return payload + "." + signature(payload, secret)
}
export function approvedBrief(token: unknown, secret: string): DatasetBrief | undefined {
  if (token == null) return undefined
  if (typeof token !== "string" || token.length > 16000) throw new Error("Invalid sample approval.")
  const [payload, supplied, extra] = token.split(".")
  const expected = signature(payload, secret)
  if (extra || !supplied || supplied.length !== expected.length || !timingSafeEqual(Buffer.from(supplied), Buffer.from(expected))) throw new Error("Invalid sample approval.")
  const value = JSON.parse(Buffer.from(payload, "base64url").toString())
  if (typeof value.expires !== "number" || value.expires < Date.now()) throw new Error("Sample approval expired. Refresh the sample.")
  return datasetBriefSchema.parse(value.brief)
}
export function prepareAnalysisAnswer(results: Map<string, AnalysisQueryResult>, input: AnswerInput, approved?: DatasetBrief) {
  const dataset = input.query_id ? results.get(input.query_id) : undefined
  if (input.query_id && !dataset) throw new Error("Select a successful dataset query from this turn.")
  const checks = input.checks.map(id => {
    const result = results.get(id)
    if (!result) throw new Error("Checks require successful queries from this turn.")
    return result
  })
  if (input.needs_sources && !results.size) throw new Error("Inspect the catalogue before recommending sources.")
  if (input.status !== "draft") {
    if (!dataset?.rows.length) throw new Error("A sample or ready dataset requires nonempty executed rows.")
    const names = input.brief.fields.map(field => field.name)
    if (new Set(names).size !== names.length || JSON.stringify(names) !== JSON.stringify(dataset.columns)) throw new Error("Dataset columns must match the draft in order.")
    if (input.brief.fields.some((field, i) => field.type.toUpperCase() !== dataset.types[i]?.toUpperCase())) throw new Error("Dataset types must match the draft.")
    if (input.needs_sources) throw new Error("Resolve missing sources before presenting a sample.")
  }
  if (input.status === "ready") {
    if (!approved || JSON.stringify(input.brief) !== JSON.stringify(approved)) throw new Error("Ready requires approval of this exact draft. Present a new sample if fields or scope changed.")
    if (dataset!.truncated) throw new Error("The returned dataset is incomplete. Explain the limit instead of marking ready.")
    if (dataset!.rows.some(row => input.brief.fields.some((field, i) => !field.nullable && row[i] == null))) throw new Error("Required fields contain missing values.")
    if (!checks.length || checks.some(check => check.rows.length !== 1 || !check.columns.length || check.rows[0].length !== check.columns.length || check.truncated || check.types.some(type => type !== "BOOLEAN") || check.rows[0].some(value => value !== true))) throw new Error("Ready requires executed, passing boolean validation checks.")
  }
  return { ...input, dataset: dataset ? { ...dataset, rows: input.status === "sample" ? dataset.rows.slice(0, 5) : dataset.rows } : null, checks }
}
