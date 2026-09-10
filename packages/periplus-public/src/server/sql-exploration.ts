import type { QueryResult } from "../types/sql"
import { queryFailure } from "./query-failure.ts"

export function createSqlExplorer(url: string, headers: Record<string, string>, signal: AbortSignal, onUnavailable: () => void = () => {}) {
  let unavailable = false
  return async (sql: string, parameters: unknown[]) => {
    if (unavailable) return { error: "The query service is unavailable for this turn. Use the evidence already returned." }
    try {
      const response = await fetch(new URL("/query/exec", url), {
        method: "POST", headers, body: JSON.stringify({ sql, parameters }), cache: "no-store",
        signal: AbortSignal.any([signal, AbortSignal.timeout(130_000)]),
      })
      if (!response.ok) {
        const failure = queryFailure(response.status, await response.json().catch(() => null))
        if (["service_busy", "service_unavailable", "storage_unavailable", "access_unavailable"].includes(failure.code)) { unavailable = true; onUnavailable() }
        return failure
      }
      const result: QueryResult = await response.json()
      const rows: unknown[][] = []
      let bytes = 0
      for (const row of result.rows.slice(0, 100)) {
        const size = JSON.stringify(row).length
        if (bytes + size > 60_000) break
        rows.push(row)
        bytes += size
      }
      return { sql: result.sql, columns: result.columns, types: result.types, rows,
        query_id: result.query_id, source_snapshot: result.source_snapshot,
        returned_rows: result.rows.length, truncated: result.truncated,
        sampled: rows.length < result.rows.length }
    } catch {
      signal.throwIfAborted()
      { unavailable = true; onUnavailable() }
      return { error: "The query service could not be reached. Query completion is unconfirmed." }
    }
  }
}
