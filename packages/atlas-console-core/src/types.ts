export interface SqlColumn {
  name: string
  data_type: string
  nullable: boolean
}

export interface SqlRelation {
  schema_name: "ingest" | "material"
  name: string
  kind: "table" | "view"
  columns: SqlColumn[]
}

export interface SqlMetadata {
  relations: SqlRelation[]
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
  kind: "keyword" | "schema" | "relation" | "column" | "command"
  description?: string
}

export interface QueryResult {
  kind: "query"
  result: SqlResult
  durationMilliseconds: number
}

export type CommandResult =
  | { kind: "clear" }
  | { kind: "exit" }
  | { kind: "message"; text: string }

export type ConsoleResult = QueryResult | CommandResult
