import { CheckCircle2Icon, Loader2Icon, XCircleIcon } from "lucide-react"
import { useMemo } from "react"

import { Badge } from "@/components/ui/badge"
import { truncateMiddle } from "@/lib/truncate"
import { cn } from "@/lib/utils"
import type { CrawlProgressEvent } from "@/types/index"

type IndexEventLogProps = {
  events: CrawlProgressEvent[]
}

export function IndexEventLog({ events }: IndexEventLogProps) {
  const orderedEvents = useMemo(() => {
    const completed = new Set(
      events
        .filter((event) => event.status !== "started")
        .map((event) => eventKey(event))
    )
    return events
      .filter((event) => event.status !== "started" || !completed.has(eventKey(event)))
  }, [events])

  if (orderedEvents.length === 0) {
    return (
      <div className="rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground">
        No progress events were captured.
      </div>
    )
  }

  return (
    <div className="h-full min-h-0 space-y-1.5 overflow-auto rounded-md border p-2">
      {orderedEvents.map((event, index) => (
        <LogEventItem
          key={`${event.status}-${event.url}-${index}`}
          event={event}
        />
      ))}
    </div>
  )
}

function eventKey(event: CrawlProgressEvent) {
  return `${event.label}\n${event.url}`
}

type LogEventItemProps = {
  event: CrawlProgressEvent
}

function LogEventItem({ event }: LogEventItemProps) {
  const Icon =
    event.status === "started"
      ? Loader2Icon
      : event.status === "succeeded"
        ? CheckCircle2Icon
        : XCircleIcon

  return (
    <div className="grid grid-cols-[auto_minmax(0,1fr)] gap-2 rounded-md px-2 py-1.5 hover:bg-muted/40">
      <Icon
        className={cn(
          "mt-0.5 size-3.5 shrink-0",
          event.status === "started" && "text-muted-foreground",
          event.status === "succeeded" && "text-emerald-500",
          event.status === "failed" && "text-destructive"
        )}
      />
      <div className="grid min-w-0 gap-0.5">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <Badge className="h-5 px-1.5 text-[11px]" variant="outline">
            {event.label}
          </Badge>
          <span className="text-xs text-muted-foreground">{event.status}</span>
          {typeof event.duration === "number" ? (
            <span className="text-xs text-muted-foreground">
              {event.duration.toFixed(2)}s
            </span>
          ) : null}
          <span
            className="min-w-0 truncate text-xs font-medium"
            title={event.url}
          >
            {truncateMiddle(event.url)}
          </span>
        </div>
        {event.error ? (
          <div className="truncate text-xs text-destructive">{event.error}</div>
        ) : null}
      </div>
    </div>
  )
}
