import { queryErrorSchema, type QueryErrorCode } from "../types/query-error.ts"

export type QueryFailure = { error: string; code: QueryErrorCode }

export function queryFailure(status: number, body: unknown): QueryFailure {
  const parsed = queryErrorSchema.safeParse(body)
  if (parsed.success) return { code: parsed.data.code, error: parsed.data.detail.slice(0, 2000) }
  // Authentication, malformed requests and proxy/network failures are not SQL failures.
  return { code: "service_unavailable", error: status === 429
    ? "Query service is busy. Retry later; do not rewrite SQL."
    : "Query service is unavailable. Do not rewrite SQL to repair a service failure." }
}
