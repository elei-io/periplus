import type { SqlMetadata, SqlResult } from "./types.js"

export class SqlApiError extends Error {
  constructor(
    message: string,
    readonly status: number
  ) {
    super(message)
    this.name = "SqlApiError"
  }
}

export class SqlApi {
  constructor(private readonly baseUrl: string) {}

  async query(sql: string, signal?: AbortSignal): Promise<SqlResult> {
    return this.request<SqlResult>("/sql/query", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ sql }),
      signal,
    })
  }

  async metadata(signal?: AbortSignal): Promise<SqlMetadata> {
    const value = await this.request<unknown>("/sql/metadata", { signal })
    if (!isSqlMetadata(value)) {
      throw new Error(
        "Atlas API returned an incompatible SQL metadata contract. " +
          "Restart or redeploy the API and try again."
      )
    }
    return value
  }

  private async request<T>(path: string, init: RequestInit): Promise<T> {
    const response = await fetch(
      new URL(path.slice(1), `${this.baseUrl.replace(/\/$/, "")}/`),
      init
    )
    if (!response.ok) {
      const body = (await response.json().catch(() => null)) as {
        detail?: unknown
      } | null
      const message =
        typeof body?.detail === "string"
          ? body.detail
          : `SQL request failed with status ${response.status}`
      throw new SqlApiError(message, response.status)
    }
    return (await response.json()) as T
  }
}

function isSqlMetadata(value: unknown): value is SqlMetadata {
  if (!value || typeof value !== "object") return false
  const candidate = value as Partial<SqlMetadata>
  return (
    typeof candidate.catalogue_version === "string" &&
    typeof candidate.duckdb_version === "string" &&
    typeof candidate.catalogue_bytes === "number" &&
    Number.isSafeInteger(candidate.catalogue_bytes) &&
    candidate.catalogue_bytes >= 0 &&
    Array.isArray(candidate.relations) &&
    Array.isArray(candidate.macros)
  )
}
