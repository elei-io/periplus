import { readSseStream } from "@/lib/sse"
import type { SseMessage } from "@/lib/sse"
import type {
  CrawlProgressEvent,
  IndexLink,
  IndexStreamEvent,
} from "@/types/index"

function parseIndexStreamEvent(message: SseMessage): IndexStreamEvent {
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
      data: data as IndexLink[],
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

export async function readIndexStream(
  response: Response,
  onEvent: (event: IndexStreamEvent) => void
) {
  await readSseStream(response, (message) => {
    onEvent(parseIndexStreamEvent(message))
  })
}
