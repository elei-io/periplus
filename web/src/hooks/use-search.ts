import { apiErrorFromResponse, apiUrl } from "@/lib/api"
import { readSseStream } from "@/lib/sse"
import type { SearchEvent } from "@/types/search"

export async function streamSearch(
  question: string,
  onEvent: (event: SearchEvent) => void,
  signal?: AbortSignal
) {
  const response = await fetch(apiUrl("/search/stream"), {
    method: "POST",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ question }),
    signal,
  })
  if (!response.ok) throw await apiErrorFromResponse(response)

  await readSseStream(response, (message) => {
    const event = JSON.parse(message.data) as SearchEvent
    if (event.type !== message.event) {
      throw new Error("The Atlas agent returned an invalid event stream.")
    }
    onEvent(event)
  })
}
