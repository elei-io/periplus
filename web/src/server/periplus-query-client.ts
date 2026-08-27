import "server-only"

import {
  isSqlQueryResult,
  type SqlQueryResult,
} from "@/types/sql"

const QUERY_TIMEOUT_MILLISECONDS = 30_000

export class PeriplusQueryError extends Error {
  constructor(
    message: string,
    readonly status: number
  ) {
    super(message)
    this.name = "PeriplusQueryError"
  }
}

function apiBaseUrl() {
  return process.env.PERIPLUS_API_URL ?? "http://127.0.0.1:8000"
}

export async function executePeriplusQuery(
  sql: string,
  signal?: AbortSignal
): Promise<SqlQueryResult> {
  const timeoutSignal = AbortSignal.timeout(QUERY_TIMEOUT_MILLISECONDS)
  const requestSignal = signal
    ? AbortSignal.any([signal, timeoutSignal])
    : timeoutSignal

  const response = await fetch(new URL("/sql/query", apiBaseUrl()), {
    method: "POST",
    headers: {
      accept: "application/json",
      "content-type": "application/json",
    },
    body: JSON.stringify({ sql }),
    cache: "no-store",
    signal: requestSignal,
  })
  const body: unknown = await response.json().catch(() => null)

  if (!response.ok) {
    const detail =
      body && typeof body === "object" && "detail" in body
        ? (body as { detail?: unknown }).detail
        : undefined
    throw new PeriplusQueryError(
      typeof detail === "string"
        ? detail
        : `Periplus query failed with status ${response.status}`,
      response.status
    )
  }
  if (!isSqlQueryResult(body)) {
    throw new PeriplusQueryError(
      "Periplus returned an incompatible query response.",
      502
    )
  }
  return body
}
