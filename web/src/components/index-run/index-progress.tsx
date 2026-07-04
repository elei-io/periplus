import { Loader2Icon } from "lucide-react"
import { useMemo } from "react"

import { Progress } from "@/components/ui/progress"
import { truncateMiddle } from "@/lib/truncate"
import type { CrawlProgressEvent } from "@/types/index"

type IndexProgressProps = {
  events: CrawlProgressEvent[]
}

export function IndexProgress({ events }: IndexProgressProps) {
  const summary = useMemo(() => {
    const started = events.filter((event) => event.status === "started").length
    const succeeded = events.filter(
      (event) => event.status === "succeeded"
    ).length
    const failed = events.filter((event) => event.status === "failed").length
    const completed = succeeded + failed
    const progress = started === 0 ? 0 : Math.round((completed / started) * 100)

    return {
      completed,
      failed,
      progress: Math.min(progress, 100),
      started,
      succeeded,
    }
  }, [events])

  const latestEvent = events.at(-1)
  const latestMessage = latestEvent
    ? `${latestEvent.status} ${latestEvent.label} ${truncateMiddle(latestEvent.url)}`
    : "Waiting for crawl activity."
  const latestFullMessage = latestEvent
    ? `${latestEvent.status} ${latestEvent.label} ${latestEvent.url}`
    : latestMessage

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

        <Progress className="h-1" value={summary.progress} />
      </div>
    </section>
  )
}
