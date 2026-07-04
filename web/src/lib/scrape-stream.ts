import { readSseStream } from "@/lib/sse"
import type { SseMessage } from "@/lib/sse"
import type { CrawlProgressEvent } from "@/types/index"
import type { ScrapeOutput, ScrapeStreamEvent } from "@/types/scrape"

function parseScrapeStreamEvent(message: SseMessage): ScrapeStreamEvent {
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
      data: data as ScrapeOutput,
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

export async function readScrapeStream(
  response: Response,
  onEvent: (event: ScrapeStreamEvent) => void
) {
  await readSseStream(response, (message) => {
    onEvent(parseScrapeStreamEvent(message))
  })
}
