export interface CrawlGraphSummary {
  id: string;
  slug: string;
  description: string | null;
  root_node_id: string | null;
  system_owned: boolean;
  created_at: string;
}

export interface CrawlGraphList {
  items: CrawlGraphSummary[];
  total: number;
}

export interface AtlasApi {
  ai: {
    ask(
      prompt: string,
      context: readonly AiMessage[],
      signal?: AbortSignal,
    ): AsyncIterable<AiEvent>;
  };
  graphs: {
    list(signal?: AbortSignal): Promise<CrawlGraphList>;
    run(
      graphId: string,
      input: GraphRunTrigger,
      signal?: AbortSignal,
    ): Promise<GraphRunSubmission>;
  };
  runs: {
    list(signal?: AbortSignal): Promise<GraphRunList>;
    get(runId: string, signal?: AbortSignal): Promise<GraphRun>;
    failures(
      runId: string,
      signal?: AbortSignal,
    ): Promise<GraphRunFailureSummary>;
    pause(runId: string, signal?: AbortSignal): Promise<GraphRun>;
    resume(runId: string, signal?: AbortSignal): Promise<GraphRun>;
    cancel(runId: string, signal?: AbortSignal): Promise<GraphRun>;
    follow(
      runId: string,
      signal?: AbortSignal,
    ): AsyncIterable<GraphRunEvent>;
  };
  catalogue: {
    execute(sql: string, signal?: AbortSignal): Promise<CatalogueQueryResult>;
    compile(
      sql: string,
      signal?: AbortSignal,
    ): Promise<CatalogueCompilationResult>;
    metadata(signal?: AbortSignal): Promise<CatalogueMetadata>;
    runtime(signal?: AbortSignal): Promise<CatalogueQueryRuntime>;
    status(signal?: AbortSignal): Promise<CatalogueStatus>;
  };
}

export interface AiMessage {
  role: "user" | "assistant";
  content: string;
}

export interface AiResponse {
  kind: "message";
  message: string;
}

export interface AiSqlSuggestion {
  title: string;
  description: string;
  authored_sql: string;
  executable_sql: string;
  compiler_fingerprint?: string | null;
  catalogue_revision?: string | null;
}

export interface AiEvent {
  type:
    | "tool.started"
    | "tool.completed"
    | "tool.failed"
    | "response.completed"
    | "response.failed";
  run_id: string;
  call_id?: string | null;
  tool?: string | null;
  duration_ms?: number | null;
  message?: string | null;
  response?: AiResponse | null;
  suggestions?: AiSqlSuggestion[];
}

export interface GraphRunTrigger {
  urls: string[];
  max_crawls?: number;
}

export interface GraphRunSubmission {
  graph_id: string;
  run_id: string;
  status: "queued";
}

export type GraphRunStatus =
  | "queued"
  | "running"
  | "paused"
  | "completed"
  | "completed_with_errors"
  | "failed"
  | "cancelled";

export interface GraphRun {
  id: string;
  graph_id: string;
  graph_slug?: string | null;
  status: GraphRunStatus;
  trigger_kind: "manual" | "schedule";
  trigger_schedule_id: string | null;
  trigger_urls: string[];
  max_crawls: number;
  crawl_limit_reached: boolean;
  request_count: number;
  pending_request_count: number;
  failed_request_count: number;
  error_count: number;
  queued_request_count?: number;
  fetching_request_count?: number;
  navigating_request_count?: number;
  created_at: string;
  started_at: string | null;
  last_progress_at: string | null;
  completed_at: string | null;
  paused_at: string | null;
  cancel_requested_at: string | null;
  error: string | null;
}

export interface GraphRunList {
  items: GraphRun[];
  total: number;
}

export interface GraphRunFailure {
  failure_stage: string;
  failure_code: string;
  status_code: number | null;
  count: number;
  example_url: string;
  example_detail: string | null;
  last_occurred_at: string;
}

export interface GraphRunFailureSummary {
  items: GraphRunFailure[];
  total: number;
}

export interface GraphNodeProgress {
  admitted: number;
  queued: number;
  crawling: number;
  awaiting_navigation: number;
  evaluating_edges: number;
  completed: number;
  failed: number;
  cancelled: number;
  settled: boolean;
}

export interface GraphEdgeProgress {
  evaluations_pending: number;
  evaluations_running: number;
  evaluations_completed: number;
  evaluations_failed: number;
  urls_selected: number;
  urls_admitted: number;
  urls_deduplicated: number;
  settled: boolean;
}

export type GraphRunEvent =
  | {
      kind: "progress";
      nodes: Record<string, GraphNodeProgress>;
      edges: Record<string, GraphEdgeProgress>;
    }
  | {
      kind: "settled";
      run: GraphRun;
    };

export interface CatalogueQueryResult {
  statementKind: "query" | "explain" | "explain_analyze";
  columns: string[];
  columnTypes: string[];
  rows: unknown[][];
}

export interface CatalogueQueryRuntime {
  transport: "api";
  mutation_policy: "read_only";
  result_format: "arrow_ipc_stream";
  cancellation_supported: boolean;
  maximum_concurrency: number;
  query_timeout_seconds: number;
  maximum_rows: number;
  maximum_result_bytes: number;
}

export interface CatalogueStatus {
  lake_slug: string;
  active_file_count: number;
  active_storage_bytes: number;
  ducklake_version: string | null;
  catalogue_schema_version: string;
  compiler_version: string;
}

export interface CatalogueCompilationDiagnostic {
  code: string;
  severity: "info" | "warning" | "error";
  message: string;
  sql_fragment?: string | null;
  documentation_anchor: string | null;
}

export interface CatalogueCompilationResult {
  valid: boolean;
  supported: boolean;
  materialization_eligible: boolean;
  outcome: "invalid" | "optimized" | "unchanged" | "unsupported";
  authored_sql: string;
  executable_sql: string | null;
  diagnostics: CatalogueCompilationDiagnostic[];
  applied_rewrites: Array<{ rule: string; evidence: string }>;
  catalogue_revision: string | null;
  compiler_version: string;
}

export interface CatalogueMetadataColumn {
  name: string;
  data_type: string;
  nullable: boolean;
}

export interface CatalogueMetadataRelation {
  catalog_name: string;
  schema_name: string;
  name: string;
  kind: "table" | "view";
  columns: CatalogueMetadataColumn[];
}

export interface CatalogueMetadataFunction {
  catalog_name: string;
  schema_name: string;
  name: string;
  kind: string;
  description: string | null;
  return_type: string | null;
  parameters: Array<{
    name: string;
    data_type: string | null;
  }>;
  varargs: string | null;
  result_columns: CatalogueMetadataColumn[];
}

export interface CatalogueMetadata {
  catalog_name: string;
  default_schema: string;
  relations: CatalogueMetadataRelation[];
  functions: CatalogueMetadataFunction[];
}

export type CompletionKind =
  | "resource"
  | "action"
  | "option"
  | "argument"
  | "keyword"
  | "schema"
  | "relation"
  | "column"
  | "function";

export interface CompletionItem {
  insertText: string;
  replaceStart: number;
  replaceEnd: number;
  kind: CompletionKind;
  description?: string;
  priority?: number;
}

export interface CompletionRequest {
  input: string;
  cursor: number;
  prefix: string;
  replaceStart: number;
  context: CommandContext;
}

export type CompletionProvider = (
  request: CompletionRequest,
) => Promise<CompletionItem[]> | CompletionItem[];

export interface CommandArgumentDefinition {
  name: string;
  description?: string;
  required?: boolean;
  choices?: readonly string[];
  complete?: CompletionProvider;
}

export interface CommandOptionDefinition {
  name: string;
  description?: string;
  type: "boolean" | "string" | "integer";
  choices?: readonly string[];
  complete?: CompletionProvider;
  repeatable?: boolean;
  minimum?: number;
  maximum?: number;
}

export type AtomicCommandResult =
  | {
      kind: "progress";
      state: "active" | "completed" | "failed";
      activity: "thinking" | "catalogue" | "sql";
      label: string;
      groupStart?: boolean;
      durationMilliseconds?: number;
    }
  | {
      kind: "assistant";
      text: string;
      sql?: string;
      sqlRunCommand?: string;
      suggestions?: Array<{
        index: number;
        title: string;
        description: string;
      }>;
      state?: "completed" | "failed";
    }
  | {
      kind: "message";
      text: string;
    }
  | {
      kind: "table";
      columns: string[];
      rows: unknown[][];
      summary?: string;
    }
  | {
      kind: "navigate";
      path: string;
      label: string;
    }
  | {
      kind: "clear";
    };

export type CommandResult =
  | AtomicCommandResult
  | {
      kind: "stream";
      events: AsyncIterable<AtomicCommandResult>;
    };

export interface CommandContext {
  api: AtlasApi;
  signal: AbortSignal;
  session: {
    history: readonly string[];
    lastRunId?: string;
    aiHistory?: AiMessage[];
    lastAiSuggestions?: AiSqlSuggestion[];
  };
  completion: {
    reload(signal?: AbortSignal): Promise<CatalogueMetadata>;
  };
}

export interface CommandInvocation {
  positionals: Readonly<Record<string, string | undefined>>;
  options: Readonly<
    Record<string, string | number | boolean | readonly string[]>
  >;
}

export interface CommandDefinition {
  path: readonly string[];
  summary: string;
  usage: string;
  examples: readonly string[];
  arguments?: readonly CommandArgumentDefinition[];
  options?: readonly CommandOptionDefinition[];
  execute(
    invocation: CommandInvocation,
    context: CommandContext,
  ): Promise<CommandResult> | CommandResult;
}
