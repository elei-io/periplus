import type { QueryResult } from "./sql"

export type AnalysisQueryResult = Pick<QueryResult, "query_id" | "sql" | "columns" | "types" | "rows" | "truncated" | "elapsed_ms" | "diagnostics" | "plan"> & { executed_at: string }
export type SelectedResult = { title: string; result: AnalysisQueryResult }
