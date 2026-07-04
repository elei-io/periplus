import { readSseStream } from "@/lib/sse"
import type { SseMessage } from "@/lib/sse"
import type { CrawlProgressEvent } from "@/types/index"
import type { ExtractOutput, ExtractStreamEvent } from "@/types/extract"

function parseExtractStreamEvent(message: SseMessage): ExtractStreamEvent {
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
      data: data as ExtractOutput,
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

export async function readExtractStream(
  response: Response,
  onEvent: (event: ExtractStreamEvent) => void
) {
  await readSseStream(response, (message) => {
    onEvent(parseExtractStreamEvent(message))
  })
}
