import type { QueryResult } from "./sql"

export type AnalysisQueryResult = Pick<QueryResult, "schema_version" | "source_snapshot" | "query_id" | "sql" | "columns" | "types" | "rows" | "truncated" | "elapsed_ms" | "diagnostics" | "plan"> & { executed_at: string }
