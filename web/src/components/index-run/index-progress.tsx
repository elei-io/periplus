import { Loader2Icon } from "lucide-react"
import { useMemo } from "react"

import { Progress } from "@/components/ui/progress"
import { truncateMiddle } from "@/lib/truncate"
import type { ProgressEvent } from "@/types/progress"

type IndexProgressProps = {
  events: ProgressEvent[]
}

export function IndexProgress({ events }: IndexProgressProps) {
  const summary = useMemo(() => {
    const latestByOperation = new Map<string, ProgressEvent>()
    for (const event of events) latestByOperation.set(event.operation_id, event)
    const operations = [...latestByOperation.values()]
    const started = operations.length
    const succeeded = operations.filter((event) => event.status === "succeeded").length
    const failed = operations.filter((event) => event.status === "failed").length
    const completed = succeeded + failed

    return {
      completed,
      failed,
      started,
      succeeded,
    }
  }, [events])

  const latestEvent = events.at(-1)
  const latestResource = latestEvent?.resource ?? ""
  const latestMessage = latestEvent
    ? [
        latestEvent.message ??
          `${latestEvent.status} ${latestEvent.phase.replaceAll("_", " ")}`,
        latestResource ? truncateMiddle(latestResource) : null,
      ]
        .filter(Boolean)
        .join(" · ")
    : "Waiting for crawl activity."
  const latestFullMessage = latestEvent
    ? [latestEvent.message ?? `${latestEvent.status} ${latestEvent.phase}`, latestResource]
        .filter(Boolean)
        .join(" · ")
    : latestMessage
  const determinateProgress =
    latestEvent &&
    typeof latestEvent.current === "number" &&
    typeof latestEvent.total === "number" &&
    latestEvent.total > 0
      ? Math.min(100, Math.round((latestEvent.current / latestEvent.total) * 100))
      : null

  return (
    <section className="w-full min-w-0 px-4 text-card-foreground">
      <div className="grid min-w-0 gap-2">
        <div className="flex min-w-0 items-center justify-between gap-4">
          <div className="flex min-w-0 items-center gap-3">
            <Loader2Icon className="size-4 shrink-0 animate-spin text-primary" />
            <p
              className="min-w-0 truncate text-xs text-muted-foreground"
              title={latestFullMessage}
            >
              {latestMessage}
            </p>
          </div>

          <div className="shrink-0 text-xs text-muted-foreground">
            {summary.completed} of {summary.started || "?"} completed
          </div>
        </div>

        <Progress className="h-1" value={determinateProgress} />
      </div>
    </section>
  )
}
