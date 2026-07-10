import { CheckCircle2Icon, Loader2Icon, XCircleIcon } from "lucide-react"
import { useMemo } from "react"

import { Badge } from "@/components/ui/badge"
import { truncateMiddle } from "@/lib/truncate"
import { cn } from "@/lib/utils"
import type { ProgressEvent } from "@/types/progress"

type IndexEventLogProps = {
  events: ProgressEvent[]
}

export function IndexEventLog({ events }: IndexEventLogProps) {
  const orderedEvents = useMemo(() => {
    const latestByOperation = new Map<string, ProgressEvent>()
    for (const event of events) {
      latestByOperation.set(event.operation_id, event)
    }
    return [...latestByOperation.values()]
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
          key={`${event.operation_id}-${index}`}
          event={event}
        />
      ))}
    </div>
  )
}

type LogEventItemProps = {
  event: ProgressEvent
}

function LogEventItem({ event }: LogEventItemProps) {
  const resource = event.resource ?? ""
  const Icon =
    event.status === "waiting" || event.status === "started"
      ? Loader2Icon
      : event.status === "succeeded"
        ? CheckCircle2Icon
        : XCircleIcon

  return (
    <div className="grid grid-cols-[auto_minmax(0,1fr)] gap-2 rounded-md px-2 py-1.5 hover:bg-muted/40">
      <Icon
        className={cn(
          "mt-0.5 size-3.5 shrink-0",
          (event.status === "waiting" || event.status === "started") && "text-muted-foreground",
          event.status === "succeeded" && "text-emerald-500",
          event.status === "failed" && "text-destructive"
        )}
      />
      <div className="grid min-w-0 gap-0.5">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <Badge className="h-5 px-1.5 text-[11px]" variant="outline">
            {event.phase.replaceAll("_", " ")}
          </Badge>
          <span className="text-xs text-muted-foreground">{event.status}</span>
          {typeof event.duration === "number" ? (
            <span className="text-xs text-muted-foreground">
              {event.duration.toFixed(2)}s
            </span>
          ) : null}
          {typeof event.current === "number" && typeof event.total === "number" ? (
            <span className="text-xs text-muted-foreground">
              {event.current}/{event.total}
            </span>
          ) : null}
          <span
            className="min-w-0 truncate text-xs font-medium"
            title={resource}
          >
            {truncateMiddle(resource)}
          </span>
        </div>
        {event.message ? (
          <div className="truncate text-xs text-muted-foreground">{event.message}</div>
        ) : null}
        {event.error ? (
          <div className="truncate text-xs text-destructive">{event.error}</div>
        ) : null}
      </div>
    </div>
  )
}
