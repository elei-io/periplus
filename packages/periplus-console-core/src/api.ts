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
  constructor(private readonly baseUrl: string, private readonly token?: string, private readonly access: "public" | "admin" = "public") {}

  async query(sql: string, signal?: AbortSignal): Promise<SqlResult> {
    const value = await this.request<unknown>(this.access === "admin" ? "/admin/sql/exec" : "/query/exec", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ sql }),
      signal,
    })
    if (!isSqlResult(value)) {
      throw new Error(
        "Periplus API returned an incompatible SQL query contract. " +
          "Restart or redeploy the API and try again."
      )
    }
    return value
  }

  async metadata(signal?: AbortSignal): Promise<SqlMetadata> {
    if (this.access === "admin") return this.request<SqlMetadata>("/sql/metadata", { method: "GET", signal })
    const relations: SqlMetadata["relations"] = []
    const version = await this.query("SELECT version()", signal)
    for (const name of ["capture", "html_element", "link", "page"]) {
      const result = await this.query(`SELECT * FROM public_v1.${name} LIMIT 0`, signal)
      relations.push({ schema_name: "public_v1", name, kind: "view", description: null,
        columns: result.columns.map((column, index) => ({ name: column, data_type: result.types[index] ?? "",
          nullable: (result.types[index] ?? "").includes("Nullable("), description: null })) })
    }
    return { engine_version: String(version.rows[0]?.[0] ?? "unknown"), relations, macros: [] }
  }

  private async request<T>(path: string, init: RequestInit): Promise<T> {
    const response = await fetch(
      new URL(path.slice(1), `${this.baseUrl.replace(/\/$/, "")}/`),
      { ...init, headers: { ...init.headers, ...(this.token ? { authorization: `Bearer ${this.token}` } : {}) } }
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

function isSqlResult(value: unknown): value is SqlResult {
  if (!value || typeof value !== "object") return false
  const candidate = value as Partial<SqlResult>
  return (
    Array.isArray(candidate.columns) &&
    candidate.columns.every((column) => typeof column === "string") &&
    Array.isArray(candidate.types) &&
    candidate.types.every((type) => typeof type === "string") &&
    Array.isArray(candidate.rows) &&
    candidate.rows.every((row) => Array.isArray(row)) &&
    typeof candidate.truncated === "boolean"
  )
}
