import { readSseStream } from "@/lib/sse"
import type { SseMessage } from "@/lib/sse"
import type { SearchResult, SearchStreamEvent } from "@/types/search"
import type { CrawlProgressEvent } from "@/types/index"

function parseSearchStreamEvent(message: SseMessage): SearchStreamEvent {
  const data = JSON.parse(message.data) as unknown

  if (message.event === "progress") {
    return {
      type: "progress",
      data: data as CrawlProgressEvent,
    }
  }

  if (message.event === "result") {
    return {
      type: "result",
      data: data as SearchResult[],
    }
  }

  if (message.event === "error") {
    return {
      type: "error",
      data: data as { message: string },
    }
  }

  return {
    type: "done",
    data: {},
  }
}

export async function readSearchStream(
  response: Response,
  onEvent: (event: SearchStreamEvent) => void
) {
  await readSseStream(response, (message) => {
    onEvent(parseSearchStreamEvent(message))
  })
}
