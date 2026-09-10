import { analysisView } from "./analysis-view.ts"
import type { DiscoveryMessage } from "../types/assistant"

// Describe observable output, not whether an answer was useful or correct.
export function workspaceOutput(message: DiscoveryMessage) {
  const view = analysisView(message)
  const evidence = view.queries.flatMap(query => query.state === "output-available" && query.output.result ? [query.output.result] : [])
  return {
    has_answer: Boolean(view.finding.trim()),
    evidence_count: evidence.length,
    has_evidence: evidence.length > 0,
    has_nonempty_evidence: evidence.some(result => result.rows.length > 0),
    has_schema: Boolean(view.schema),
    has_dataset: Boolean(view.presentation?.dataset?.rows.length),
    has_coverage_suggestion: view.coverage.length > 0,
    dataset_status: view.presentation?.status ?? "none",
    row_count: view.presentation?.dataset?.rows.length ?? 0,
    truncated: view.presentation?.dataset?.truncated ?? false,
    issue_count: view.presentation?.issues.length ?? 0,
    sql_error_count: view.queries.filter(query => query.state === "output-error" || (query.state === "output-available" && query.output.error)).length,
  }
}
