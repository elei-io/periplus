import { readSseStream } from "@/lib/sse"
import type { SseMessage } from "@/lib/sse"
import type { CrawlProgressEvent } from "@/types/index"
import type { CalibrateStreamEvent, CalibrationOutput } from "@/types/calibrate"

function parseCalibrateStreamEvent(message: SseMessage): CalibrateStreamEvent {
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
      data: data as CalibrationOutput,
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

export async function readCalibrateStream(
  response: Response,
  onEvent: (event: CalibrateStreamEvent) => void
) {
  await readSseStream(response, (message) => {
    onEvent(parseCalibrateStreamEvent(message))
  })
}
