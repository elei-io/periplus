import type { SqlMetadata, SqlResult } from "./types.js"

const PUBLIC_SCHEMAS = ["public_v1"] as const

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
    const versionResult = await this.query(
      "SELECT version() AS duckdb_version",
      signal
    )
    const duckdbVersion = versionResult.rows[0]?.[0]
    if (typeof duckdbVersion !== "string") {
      throw new Error("Periplus API did not return the DuckDB version.")
    }

    const relations: SqlMetadata["relations"] = []
    const schemas = this.access === "admin"
      ? (await this.query("SELECT schema_name FROM information_schema.schemata WHERE catalog_name = current_database() ORDER BY schema_name", signal)).rows.map((row) => String(row[0]))
      : PUBLIC_SCHEMAS
    for (const schemaName of schemas) {
      const tables = await this.query(
        this.access === "admin"
          ? `SELECT table_name, table_type FROM information_schema.tables WHERE table_catalog = current_database() AND table_schema = '${schemaName.replaceAll("'", "''")}' ORDER BY table_name`
          : `SHOW TABLES FROM ${quoteIdentifier(schemaName)}`,
        signal
      )
      for (const row of tables.rows) {
        const name = row[0]
        if (typeof name !== "string") {
          throw new Error("Periplus API returned an invalid table name.")
        }
        const description = await this.query(
          `DESCRIBE ${quoteIdentifier(schemaName)}.${quoteIdentifier(name)}`,
          signal
        )
        relations.push({
          schema_name: schemaName,
          name,
          kind: this.access === "admin" && row[1] === "BASE TABLE" ? "table" : "view",
          description: null,
          columns: description.rows.map((column) => {
            if (
              typeof column[0] !== "string" ||
              typeof column[1] !== "string"
            ) {
              throw new Error(
                "Periplus API returned an invalid column description."
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

function quoteIdentifier(value: string): string {
  return `"${value.replaceAll('"', '""')}"`
}
