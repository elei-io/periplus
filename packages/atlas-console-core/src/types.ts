export interface SqlColumn {
  name: string
  data_type: string
  nullable: boolean
  description: string | null
}

export type PublicSchemaName = "web" | "dom"

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
  relations: SqlRelation[]
  macros: SqlMacro[]
}

export interface SqlResult {
  columns: string[]
  types: string[]
  rows: unknown[][]
  truncated: boolean
}

export interface AiMessage {
  role: "user" | "assistant"
  content: string
}

export interface AiSqlSuggestion {
  title: string
  description: string
  sql: string
  display_sql: string
}

export interface AiAnswer {
  conclusion: string
  evidence: string[]
  recommendation: string | null
}

export interface AiEvent {
  type:
    | "tool.started"
    | "tool.completed"
    | "tool.failed"
    | "response.completed"
    | "response.failed"
  run_id: string
  call_id: string | null
  tool: string | null
  activity: "catalogue" | "query" | "draft" | null
  purpose: string | null
  sql: string | null
  display_sql: string | null
  row_count: number | null
  truncated: boolean | null
  duration_ms: number | null
  message: string | null
  response: AiAnswer | null
  suggestions: AiSqlSuggestion[]
}

export interface AiStreamResult {
  kind: "ai"
  events: AsyncIterable<AiEvent>
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
  | AiStreamResult

export type ConsoleResult = QueryResult | CommandResult
