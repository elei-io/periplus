import { apiErrorFromResponse, apiUrl } from "@/lib/api"
import { readSseStream } from "@/lib/sse"
import type {
  AssistantEvent,
  AssistantMessage,
  AssistantSqlResult,
} from "@/types/assistant"

export async function streamAssistantTurn(
  prompt: string,
  context: readonly AssistantMessage[],
  signal: AbortSignal,
  onEvent: (event: AssistantEvent) => void
) {
  const response = await fetch(apiUrl("/ai/stream"), {
    method: "POST",
    headers: {
      accept: "text/event-stream",
      "content-type": "application/json",
    },
    body: JSON.stringify({ prompt, context }),
    signal,
  })

  if (!response.ok) {
    throw await apiErrorFromResponse(response)
  }

  await readSseStream(response, (message) => {
    const event: unknown = JSON.parse(message.data)

    if (!isAssistantEvent(event)) {
      throw new Error("Atlas returned an incompatible assistant event.")
    }
    onEvent(event)
  })
}

export async function runAssistantSql(
  sql: string,
  signal?: AbortSignal
): Promise<AssistantSqlResult> {
  const response = await fetch(apiUrl("/sql/query"), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ sql }),
    signal,
  })

  if (!response.ok) {
    throw await apiErrorFromResponse(response)
  }

  return (await response.json()) as AssistantSqlResult
}

function isAssistantEvent(value: unknown): value is AssistantEvent {
  if (!value || typeof value !== "object") {
    return false
  }

  const event = value as Partial<AssistantEvent>
  return (
    typeof event.type === "string" &&
    typeof event.run_id === "string" &&
    Array.isArray(event.suggestions)
  )
}
