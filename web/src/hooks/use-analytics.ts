import { apiErrorFromResponse, apiUrl } from "@/lib/api"
import { readSseStream } from "@/lib/sse"
import type { AnalyticsEvent } from "@/types/analytics"

export async function streamAnalyticsQuestion(
  question: string,
  onEvent: (event: AnalyticsEvent) => void,
  signal?: AbortSignal
) {
  const response = await fetch(apiUrl("/analytics/questions/stream"), {
    method: "POST",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ question }),
    signal,
  })
  if (!response.ok) throw await apiErrorFromResponse(response)

  await readSseStream(response, (messageEvent) => {
    const event = JSON.parse(messageEvent.data) as AnalyticsEvent
    if (event.type !== messageEvent.event) {
      throw new Error("Atlas returned an invalid analytics event stream.")
    }
    onEvent(event)
  })
}
