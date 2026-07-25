import { RecordBatchReader } from "apache-arrow";
import { ApiError } from "./errors.js";
import type {
  AtlasApi,
  AiEvent,
  AiMessage,
  CatalogueCompilationResult,
  CatalogueMetadata,
  CatalogueQueryResult,
  CatalogueQueryRuntime,
  CatalogueStatus,
  CrawlGraphList,
  GraphEdgeProgress,
  GraphNodeProgress,
  GraphRun,
  GraphRunEvent,
  GraphRunFailureSummary,
  GraphRunList,
  GraphRunSubmission,
  GraphRunTrigger,
} from "./types.js";

interface CatalogueQueryState {
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  error: string | null;
}

const statementKinds = new Set<CatalogueQueryResult["statementKind"]>([
  "query",
  "explain",
  "explain_analyze",
]);

export class HttpAtlasApi implements AtlasApi {
  readonly ai = {
    ask: (
      prompt: string,
      context: readonly AiMessage[],
      signal?: AbortSignal,
    ) => this.streamAi(prompt, context, signal),
  };

  readonly graphs = {
    list: (signal?: AbortSignal) =>
      this.get<CrawlGraphList>("/crawl-graphs/", signal),
    run: (
      graphId: string,
      input: GraphRunTrigger,
      signal?: AbortSignal,
    ) =>
      this.json<GraphRunSubmission>(
        `/crawl-graphs/${encodeURIComponent(graphId)}/runs`,
        { method: "POST", body: input },
        signal,
      ),
  };

  readonly runs = {
    list: (signal?: AbortSignal) =>
      this.get<GraphRunList>("/graph-runs/", signal),
    get: (runId: string, signal?: AbortSignal) =>
      this.get<GraphRun>(
        `/graph-runs/${encodeURIComponent(runId)}`,
        signal,
      ),
    failures: (runId: string, signal?: AbortSignal) =>
      this.get<GraphRunFailureSummary>(
        `/graph-runs/${encodeURIComponent(runId)}/failure-summary`,
        signal,
      ),
    pause: (runId: string, signal?: AbortSignal) =>
      this.json<GraphRun>(
        `/graph-runs/${encodeURIComponent(runId)}/pause`,
        { method: "POST" },
        signal,
      ),
    resume: (runId: string, signal?: AbortSignal) =>
      this.json<GraphRun>(
        `/graph-runs/${encodeURIComponent(runId)}/resume`,
        { method: "POST" },
        signal,
      ),
    cancel: (runId: string, signal?: AbortSignal) =>
      this.json<GraphRun>(
        `/graph-runs/${encodeURIComponent(runId)}/cancel`,
        { method: "POST" },
        signal,
      ),
    follow: (runId: string, signal?: AbortSignal) =>
      this.followGraphRun(runId, signal),
  };

  readonly catalogue = {
    execute: (sql: string, signal?: AbortSignal) =>
      this.executeCatalogueQuery(sql, signal),
    compile: (sql: string, signal?: AbortSignal) =>
      this.json<CatalogueCompilationResult>(
        "/catalogue/sql/compile",
        { method: "POST", body: { sql, purpose: "interactive" } },
        signal,
      ),
    metadata: (signal?: AbortSignal) =>
      this.get<CatalogueMetadata>("/catalogue/metadata", signal),
    runtime: (signal?: AbortSignal) =>
      this.get<CatalogueQueryRuntime>("/catalogue/query-runtime", signal),
    status: (signal?: AbortSignal) =>
      this.get<CatalogueStatus>("/catalogue/status", signal),
  };

  constructor(
    private readonly baseUrl: string,
    private readonly request: typeof fetch = globalThis.fetch.bind(globalThis),
  ) {}

  private async get<T>(path: string, signal?: AbortSignal): Promise<T> {
    const response = await this.request(
      new URL(path.replace(/^\//, ""), `${this.baseUrl.replace(/\/$/, "")}/`),
      {
        headers: { Accept: "application/json" },
        signal,
      },
    );
    if (!response.ok) {
      const body = await response.json().catch(() => null) as unknown;
      const detail =
        body && typeof body === "object" && "detail" in body
          ? (body as { detail: unknown }).detail
          : body;
      throw new ApiError(
        typeof detail === "string"
          ? detail
          : `Atlas API returned ${response.status}.`,
        response.status,
        detail,
      );
    }
    return response.json() as Promise<T>;
  }

  private async json<T>(
    path: string,
    options: {
      method: "POST";
      body?: unknown;
    },
    signal?: AbortSignal,
  ): Promise<T> {
    const response = await this.request(this.url(path), {
      method: options.method,
      headers: options.body === undefined
        ? { Accept: "application/json" }
        : {
            Accept: "application/json",
            "Content-Type": "application/json",
          },
      body: options.body === undefined
        ? undefined
        : JSON.stringify(options.body),
      signal,
    });
    if (!response.ok) throw await apiError(response);
    return response.json() as Promise<T>;
  }

  private async *followGraphRun(
    runId: string,
    signal?: AbortSignal,
  ): AsyncIterable<GraphRunEvent> {
    const response = await this.request(
      this.url(`/graph-runs/${encodeURIComponent(runId)}/events`),
      {
        headers: { Accept: "text/event-stream" },
        signal,
      },
    );
    if (!response.ok) throw await apiError(response);
    if (!response.body) {
      throw new Error("Graph run event response has no body.");
    }
    const reader = response.body
      .pipeThrough(new TextDecoderStream())
      .getReader();
    let buffer = "";
    try {
      while (true) {
        const { value, done } = await reader.read();
        buffer += value ?? "";
        let boundary = buffer.indexOf("\n\n");
        while (boundary >= 0) {
          const block = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          const event = parseServerEvent(block);
          if (event?.name === "progress_snapshot") {
            const value = JSON.parse(event.data) as {
              nodes: Record<string, GraphNodeProgress>;
              edges: Record<string, GraphEdgeProgress>;
            };
            yield { kind: "progress", nodes: value.nodes, edges: value.edges };
          } else if (event?.name === "run_settled") {
            yield {
              kind: "settled",
              run: JSON.parse(event.data) as GraphRun,
            };
            return;
          }
          boundary = buffer.indexOf("\n\n");
        }
        if (done) return;
      }
    } finally {
      reader.releaseLock();
    }
  }

  private async *streamAi(
    prompt: string,
    context: readonly AiMessage[],
    signal?: AbortSignal,
  ): AsyncIterable<AiEvent> {
    const response = await this.request(this.url("/ai/stream"), {
      method: "POST",
      headers: {
        Accept: "text/event-stream",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ prompt, context }),
      signal,
    });
    if (!response.ok) throw await apiError(response);
    if (!response.body) throw new Error("Atlas AI response has no body.");
    for await (const event of serverEvents(response.body)) {
      yield JSON.parse(event.data) as AiEvent;
    }
  }

  private async executeCatalogueQuery(
    sql: string,
    signal?: AbortSignal,
  ): Promise<CatalogueQueryResult> {
    const response = await this.request(
      this.url("/catalogue/query-executions"),
      {
        method: "POST",
        headers: {
          Accept: "application/vnd.apache.arrow.stream",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ sql }),
        signal,
      },
    );
    if (!response.ok) throw await apiError(response);

    const queryId = response.headers.get("X-Atlas-Query-ID");
    const statementKind = response.headers.get(
      "X-Atlas-Statement-Kind",
    ) as CatalogueQueryResult["statementKind"] | null;
    if (!queryId || !statementKind || !statementKinds.has(statementKind)) {
      throw new Error(
        "Catalogue query response is missing its execution metadata.",
      );
    }

    const cancel = () => {
      void this.request(
        this.url(`/catalogue/query-executions/${encodeURIComponent(queryId)}`),
        { method: "DELETE" },
      ).catch(() => undefined);
    };
    signal?.addEventListener("abort", cancel, { once: true });

    let columns: string[] = [];
    let columnTypes: string[] = [];
    const rows: unknown[][] = [];
    let streamError: unknown;
    try {
      const reader = await RecordBatchReader.from(response);
      await reader.open();
      columns = reader.schema.fields.map((field) => field.name);
      columnTypes = reader.schema.fields.map((field) => String(field.type));
      for await (const batch of reader) {
        const vectors = batch.schema.fields.map((field, index) => ({
          type: String(field.type),
          vector: batch.getChildAt(index),
        }));
        for (let rowIndex = 0; rowIndex < batch.numRows; rowIndex += 1) {
          rows.push(
            vectors.map(({ type, vector }) =>
              formatArrowValue(type, vector?.get(rowIndex)),
            ),
          );
        }
      }
    } catch (reason) {
      streamError = reason;
    }

    try {
      const state = await this.waitForQuery(queryId, signal);
      if (state.status !== "succeeded") {
        throw new Error(state.error ?? `Catalogue query ${state.status}.`);
      }
      if (streamError) throw streamError;
      return { statementKind, columns, columnTypes, rows };
    } finally {
      signal?.removeEventListener("abort", cancel);
    }
  }

  private async waitForQuery(
    queryId: string,
    signal?: AbortSignal,
  ): Promise<CatalogueQueryState> {
    for (let attempt = 0; attempt < 50; attempt += 1) {
      const state = await this.get<CatalogueQueryState>(
        `/catalogue/query-executions/${encodeURIComponent(queryId)}`,
        signal,
      );
      if (["succeeded", "failed", "cancelled"].includes(state.status)) {
        return state;
      }
      await abortableDelay(100, signal);
    }
    throw new Error("Catalogue query status did not settle after streaming.");
  }

  private url(path: string): URL {
    return new URL(
      path.replace(/^\//, ""),
      `${this.baseUrl.replace(/\/$/, "")}/`,
    );
  }
}

function parseServerEvent(
  block: string,
): { name: string; data: string } | undefined {
  let name = "message";
  const data: string[] = [];
  for (const line of block.replaceAll("\r\n", "\n").split("\n")) {
    if (line.startsWith("event:")) name = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  return data.length ? { name, data: data.join("\n") } : undefined;
}

async function* serverEvents(
  body: ReadableStream<Uint8Array>,
): AsyncIterable<{ name: string; data: string }> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += value ? decoder.decode(value, { stream: !done }) : "";
      buffer = buffer.replaceAll("\r\n", "\n");
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const event = parseServerEvent(buffer.slice(0, boundary));
        buffer = buffer.slice(boundary + 2);
        if (event) yield event;
        boundary = buffer.indexOf("\n\n");
      }
      if (done) return;
    }
  } finally {
    reader.releaseLock();
  }
}

async function apiError(response: Response): Promise<ApiError> {
  const body = await response.json().catch(() => null) as unknown;
  const detail =
    body && typeof body === "object" && "detail" in body
      ? (body as { detail: unknown }).detail
      : body;
  return new ApiError(
    typeof detail === "string"
      ? detail
      : `Atlas API returned ${response.status}.`,
    response.status,
    detail,
  );
}

function formatArrowValue(type: string, value: unknown): unknown {
  if (value === null || value === undefined || !type.startsWith("Timestamp<")) {
    return value;
  }
  if (value instanceof Date) return value.toISOString();
  if (typeof value === "number") return new Date(value).toISOString();
  if (typeof value === "bigint") {
    const unit = type.slice("Timestamp<".length).split(",", 1)[0];
    const milliseconds =
      unit === "SECOND"
        ? value * 1_000n
        : unit === "MILLISECOND"
          ? value
          : unit === "MICROSECOND"
            ? value / 1_000n
            : value / 1_000_000n;
    return new Date(Number(milliseconds)).toISOString();
  }
  return value;
}

async function abortableDelay(
  milliseconds: number,
  signal?: AbortSignal,
): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(resolve, milliseconds);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(signal.reason);
      },
      { once: true },
    );
  });
}
