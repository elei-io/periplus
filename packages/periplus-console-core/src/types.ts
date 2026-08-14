export interface SqlColumn {
  name: string
  data_type: string
  nullable: boolean
  description: string | null
}

export type PublicSchemaName = "web" | "content"

export interface SqlRelation {
  schema_name: PublicSchemaName
  name: string
  kind: "view"
  description: string | null
  columns: SqlColumn[]
}

export interface SqlMacroParameter {
  name: string
  data_type: string
}

export interface SqlMacro {
  schema_name: PublicSchemaName
  name: string
  kind: "scalar_macro" | "table_macro"
  parameters: SqlMacroParameter[]
  return_type: string | null
  columns: SqlColumn[]
}

export interface SqlMetadata {
  catalogue_version: string
  duckdb_version: string
  catalogue_bytes: number
  relations: SqlRelation[]
  macros: SqlMacro[]
}

export interface SqlResult {
  columns: string[]
  types: string[]
  rows: unknown[][]
  truncated: boolean
}

export interface Completion {
  value: string
  replaceStart: number
  replaceEnd: number
  kind:
    | "keyword"
    | "schema"
    | "relation"
    | "column"
    | "function"
    | "command"
    | "argument"
  description?: string
  priority?: number
}

export interface QueryResult {
  kind: "query"
  result: SqlResult
  durationMilliseconds: number
}

export interface TableResult {
  kind: "table"
  columns: string[]
  rows: unknown[][]
  summary?: string
}

export type CommandResult =
  | { kind: "clear" }
  | { kind: "exit" }
  | { kind: "message"; text: string }
  | TableResult

export type ConsoleResult = QueryResult | CommandResult
