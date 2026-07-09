import { readSseStream } from "@/lib/sse"
import type { SseMessage } from "@/lib/sse"
import type { CrawlProgressEvent } from "@/types/index"
import type { PaginateOutput, PaginateStreamEvent } from "@/types/paginate"

function parsePaginateStreamEvent(message: SseMessage): PaginateStreamEvent {
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
      data: data as PaginateOutput,
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

export async function readPaginateStream(
  response: Response,
  onEvent: (event: PaginateStreamEvent) => void
) {
  await readSseStream(response, (message) => {
    onEvent(parsePaginateStreamEvent(message))
  })
}
