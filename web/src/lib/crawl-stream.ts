import { readSseStream } from "@/lib/sse"
import type { SseMessage } from "@/lib/sse"
import type { CrawlProgressEvent } from "@/types/index"
import type { CrawlOutput, CrawlStreamEvent } from "@/types/crawl"

function parseCrawlStreamEvent(message: SseMessage): CrawlStreamEvent {
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
      data: data as CrawlOutput,
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

export async function readCrawlStream(
  response: Response,
  onEvent: (event: CrawlStreamEvent) => void
) {
  await readSseStream(response, (message) => {
    onEvent(parseCrawlStreamEvent(message))
  })
}
