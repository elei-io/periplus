import "server-only"

import { isSqlQueryResult, type SqlQueryResult } from "@/types/sql"
import { PeriplusError, periplusRequest } from "@/server/periplus-client"

export async function executePeriplusQuery(sql: string, signal?: AbortSignal): Promise<SqlQueryResult> {
  const body = await periplusRequest("/sql/query", { method: "POST", body: JSON.stringify({ sql }), signal })
  if (!isSqlQueryResult(body)) throw new PeriplusError("Periplus returned an incompatible query response.", 502)
  return body
}
