import type { SqlMetadata, SqlResult } from "./types.js"

const PUBLIC_SCHEMAS = ["web", "content"] as const

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
    const value = await this.request<unknown>("/sql/query", {
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
    const versionResult = await this.query(
      "SELECT version() AS duckdb_version",
      signal
    )
    const duckdbVersion = versionResult.rows[0]?.[0]
    if (typeof duckdbVersion !== "string") {
      throw new Error("Periplus API did not return the DuckDB version.")
    }

    const relations: SqlMetadata["relations"] = []
    for (const schemaName of PUBLIC_SCHEMAS) {
      const tables = await this.query(`SHOW TABLES FROM ${schemaName}`, signal)
      for (const row of tables.rows) {
        const name = row[0]
        if (typeof name !== "string") {
          throw new Error("Periplus API returned an invalid public table name.")
        }
        const description = await this.query(
          `DESCRIBE ${schemaName}.${quoteIdentifier(name)}`,
          signal
        )
        relations.push({
          schema_name: schemaName,
          name,
          kind: "view",
          description: null,
          columns: description.rows.map((column) => {
            if (
              typeof column[0] !== "string" ||
              typeof column[1] !== "string"
            ) {
              throw new Error(
                "Periplus API returned an invalid public column description."
              )
            }
            return {
              name: column[0],
              data_type: column[1],
              nullable: String(column[2]).toUpperCase() === "YES",
              description: null,
            }
          }),
        })
      }
    }
    return {
      duckdb_version: duckdbVersion,
      relations,
      macros: [],
    }
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

function quoteIdentifier(value: string): string {
  return `"${value.replaceAll('"', '""')}"`
}
