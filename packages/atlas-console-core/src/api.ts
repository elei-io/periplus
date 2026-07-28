import type {
  AiEvent,
  AiMessage,
  SqlMetadata,
  SqlResult,
} from "./types.js"

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
    const value = await this.request<unknown>("/sql/metadata", { signal })
    if (!isSqlMetadata(value)) {
      throw new Error(
        "Atlas API returned an incompatible SQL metadata contract. " +
          "Restart or redeploy the API and try again.",
      )
    }
    return value
  }

  async *ai(
    prompt: string,
    context: readonly AiMessage[],
    signal?: AbortSignal,
  ): AsyncIterable<AiEvent> {
    const response = await fetch(
      new URL("ai/stream", `${this.baseUrl.replace(/\/$/, "")}/`),
      {
        method: "POST",
        headers: {
          accept: "text/event-stream",
          "content-type": "application/json",
        },
        body: JSON.stringify({ prompt, context }),
        signal,
      },
    )
    if (!response.ok) {
      const body = (await response.json().catch(() => null)) as {
        detail?: unknown
      } | null
      const message =
        typeof body?.detail === "string"
          ? body.detail
          : `AI request failed with status ${response.status}`
      throw new SqlApiError(message, response.status)
    }
    if (!response.body) throw new Error("Atlas AI returned no event stream.")

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ""
    try {
      while (true) {
        const { done, value } = await reader.read()
        buffer += decoder.decode(value, { stream: !done }).replaceAll(
          "\r\n",
          "\n",
        )
        let boundary = buffer.indexOf("\n\n")
        while (boundary >= 0) {
          const frame = buffer.slice(0, boundary)
          buffer = buffer.slice(boundary + 2)
          const data = frame
            .split("\n")
            .filter((line) => line.startsWith("data:"))
            .map((line) => line.slice(5).trimStart())
            .join("\n")
          if (data) {
            const event: unknown = JSON.parse(data)
            if (!isAiEvent(event)) {
              throw new Error("Atlas API returned an incompatible AI event.")
            }
            yield event
          }
          boundary = buffer.indexOf("\n\n")
        }
        if (done) break
      }
    } finally {
      reader.releaseLock()
    }
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

function isAiEvent(value: unknown): value is AiEvent {
  if (!value || typeof value !== "object") return false
  const candidate = value as Partial<AiEvent>
  return (
    typeof candidate.type === "string" &&
    typeof candidate.run_id === "string" &&
    Array.isArray(candidate.suggestions)
  )
}

function isSqlMetadata(value: unknown): value is SqlMetadata {
  if (!value || typeof value !== "object") return false
  const candidate = value as Partial<SqlMetadata>
  return (
    typeof candidate.catalogue_version === "string" &&
    Array.isArray(candidate.relations) &&
    Array.isArray(candidate.macros)
  )
}
