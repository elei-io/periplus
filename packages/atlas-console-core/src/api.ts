import type { SqlMetadata, SqlResult } from "./types.js"

export class SqlApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
    this.name = "SqlApiError"
  }
}

export class SqlApi {
  constructor(private readonly baseUrl: string) {}

  async query(sql: string, signal?: AbortSignal): Promise<SqlResult> {
    return this.request<SqlResult>(
      "/sql/query",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ sql }),
        signal,
      },
    )
  }

  async metadata(signal?: AbortSignal): Promise<SqlMetadata> {
    return this.request<SqlMetadata>("/sql/metadata", { signal })
  }

  private async request<T>(path: string, init: RequestInit): Promise<T> {
    const response = await fetch(
      new URL(path.slice(1), `${this.baseUrl.replace(/\/$/, "")}/`),
      init,
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
