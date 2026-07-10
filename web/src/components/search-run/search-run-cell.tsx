import { useQuery, useQueryClient } from "@tanstack/react-query"
import {
  CheckIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  CircleDashedIcon,
  CircleXIcon,
  SearchIcon,
  XIcon,
} from "lucide-react"
import { AnimatePresence, motion, useReducedMotion } from "motion/react"
import { useEffect, useRef, useState } from "react"

import { SearchResults } from "@/components/search-run/search-results"
import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useCancelSearchRun } from "@/hooks/use-search-runs"
import { apiErrorFromResponse, apiUrl } from "@/lib/api"
import { readSseStream } from "@/lib/sse"
import { truncateMiddle } from "@/lib/truncate"
import { cn } from "@/lib/utils"
import type { ProgressEvent } from "@/types/progress"
import { searchProviders } from "@/types/search"
import type { SearchInput, SearchResult } from "@/types/search"
import type { TaskProgressEnvelope, TaskRunRecord } from "@/types/tasks"

const activeStatuses = new Set(["queued", "running"])

type SearchRunCellProps = {
  run: TaskRunRecord
  density: "live" | "recent"
}

export function SearchRunCell({ run, density }: SearchRunCellProps) {
  const [events, setEvents] = useState<ProgressEvent[]>([])
  const [expanded, setExpanded] = useState(false)
  const [now, setNow] = useState(() => Date.now())
  const seenEventIds = useRef(new Set<string>())
  const queryClient = useQueryClient()
  const cancelMutation = useCancelSearchRun()
  const reduceMotion = useReducedMotion()
  const active = activeStatuses.has(run.status)
  const input = run.input_json as SearchInput
  const latest = events.at(-1)

  useEffect(() => {
    if (!active) return undefined
    const timer = window.setInterval(() => setNow(Date.now()), 1_000)
    return () => window.clearInterval(timer)
  }, [active])

  useEffect(() => {
    if (!active) return undefined
    const controller = new AbortController()
    async function connect() {
      while (!controller.signal.aborted) {
        try {
          const response = await fetch(
            apiUrl(`/task-runs/${run.id}/progress`),
            {
              headers: { Accept: "text/event-stream" },
              signal: controller.signal,
            }
          )
          if (!response.ok) throw await apiErrorFromResponse(response)
          await readSseStream(response, (message) => {
            const envelope = JSON.parse(message.data) as TaskProgressEnvelope
            if (
              message.event === "progress" &&
              !seenEventIds.current.has(envelope.event_id)
            ) {
              seenEventIds.current.add(envelope.event_id)
              setEvents((current) => [
                ...current,
                envelope.data as ProgressEvent,
              ])
            }
            if (
              ["succeeded", "failed", "cancelled", "skipped"].includes(
                message.event
              )
            ) {
              void queryClient.invalidateQueries({
                queryKey: ["task-runs", "search"],
              })
            }
          })
        } catch {
          if (controller.signal.aborted) return
        }
        await new Promise((resolve) => window.setTimeout(resolve, 2_000))
      }
    }
    void connect()
    return () => controller.abort()
  }, [active, queryClient, run.id])

  const resultQuery = useQuery({
    queryKey: ["task-run-result", run.id],
    queryFn: async () => {
      const response = await fetch(apiUrl(`/task-runs/${run.id}/result`))
      if (!response.ok) throw await apiErrorFromResponse(response)
      return (await response.json()) as SearchResult[]
    },
    enabled: expanded && run.status === "succeeded",
  })

  const latestText = latest
    ? [latest.message, latest.resource ? truncateMiddle(latest.resource) : null]
        .filter(Boolean)
        .join(" · ")
    : run.status === "queued"
      ? "Waiting for a worker…"
      : active
        ? "Getting things moving…"
        : terminalMessage(run)
  const progressEvent = events.findLast(
    (event) => event.phase === "search_provider"
  )
  const progressValue =
    progressEvent?.current !== undefined && progressEvent.total
      ? Math.min(100, (progressEvent.current / progressEvent.total) * 100)
      : null
  const liveResultCount = getMetadataNumber(progressEvent, "results")
  const savedResultCount = getSavedResultCount(run)
  const resultCount = liveResultCount ?? savedResultCount
  const elapsedFrom = run.started_at ?? run.queued_at
  const elapsedTo = run.finished_at ? new Date(run.finished_at).getTime() : now
  const elapsedSeconds = Math.max(
    0,
    Math.round((elapsedTo - new Date(elapsedFrom).getTime()) / 1000)
  )
  const providerLabel =
    searchProviders.find((provider) => provider.value === input.provider)
      ?.label ?? input.provider
  const messageKey = `${latest?.operation_id ?? run.status}-${latest?.status ?? "idle"}-${latest?.message ?? ""}-${latest?.current ?? ""}`

  const rowContent = (
    <>
      <StatusMark status={run.status} reduceMotion={reduceMotion} />
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate text-sm font-medium">{input.query}</span>
          {active && resultCount !== null && resultCount > 0 ? (
            <span className="shrink-0 rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary tabular-nums">
              {resultCount} found
            </span>
          ) : null}
        </div>
        <div className="mt-0.5 flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
          <AnimatePresence initial={false} mode="wait">
            <motion.span
              key={messageKey}
              className="block truncate"
              initial={reduceMotion ? false : { opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={reduceMotion ? { opacity: 0 } : { opacity: 0, y: -4 }}
              transition={{ duration: reduceMotion ? 0 : 0.16 }}
              title={latestText}
            >
              {latestText}
            </motion.span>
          </AnimatePresence>
        </div>
      </div>
      <div className="hidden shrink-0 items-center gap-2 text-xs text-muted-foreground sm:flex">
        <span>
          {providerLabel} · {input.max_pages}{" "}
          {input.max_pages === 1 ? "page" : "pages"}
        </span>
        <span className="text-border">/</span>
        <span className="min-w-8 text-right tabular-nums">
          {formatElapsed(elapsedSeconds)}
        </span>
      </div>
      {active ? (
        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                size="icon-sm"
                variant="ghost"
                aria-label={`Cancel search for ${input.query}`}
                disabled={cancelMutation.isPending}
                onClick={() => cancelMutation.mutate(run.id)}
              />
            }
          >
            <XIcon />
          </TooltipTrigger>
          <TooltipContent>Cancel run</TooltipContent>
        </Tooltip>
      ) : run.status === "succeeded" ? (
        <span className="ml-1 flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
          {resultCount !== null ? (
            <span className="hidden tabular-nums md:inline">
              {resultCount} {resultCount === 1 ? "result" : "results"}
            </span>
          ) : null}
          {expanded ? (
            <ChevronDownIcon className="size-4" />
          ) : (
            <ChevronRightIcon className="size-4" />
          )}
        </span>
      ) : null}
    </>
  )

  const rowClassName = cn(
    "relative flex w-full min-w-0 items-center gap-3 px-4 text-left transition-colors outline-none focus-visible:bg-muted/50",
    density === "live"
      ? "search-run-live min-h-[4.5rem] py-3 hover:bg-muted/25"
      : "min-h-[3.5rem] py-2.5 hover:bg-muted/35"
  )

  return (
    <Collapsible
      className="relative"
      open={expanded}
      onOpenChange={setExpanded}
    >
      {run.status === "succeeded" ? (
        <CollapsibleTrigger className={rowClassName}>
          {rowContent}
        </CollapsibleTrigger>
      ) : (
        <div className={rowClassName}>{rowContent}</div>
      )}

      {active ? (
        <div className="absolute inset-x-0 bottom-0 h-px overflow-hidden bg-primary/10">
          {progressValue === null ? (
            <div className="search-run-progress-indeterminate h-full w-1/3 bg-primary/70" />
          ) : (
            <motion.div
              className="h-full bg-primary/80"
              initial={false}
              animate={{ width: `${progressValue}%` }}
              transition={{
                duration: reduceMotion ? 0 : 0.35,
                ease: "easeOut",
              }}
            />
          )}
        </div>
      ) : null}

      <CollapsibleContent className="flex h-[var(--collapsible-panel-height)] flex-col justify-end overflow-hidden transition-[height] duration-300 ease-out data-ending-style:h-0 data-starting-style:h-0 motion-reduce:transition-none [&[hidden]:not([hidden='until-found'])]:hidden">
        <div className="border-t bg-background/35 p-4">
          {resultQuery.isPending ? (
            <p className="text-sm text-muted-foreground">Loading results…</p>
          ) : resultQuery.isError ? (
            <p className="text-sm text-destructive">
              {resultQuery.error.message}
            </p>
          ) : (
            <SearchResults results={resultQuery.data ?? []} />
          )}
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}

function StatusMark({
  status,
  reduceMotion,
}: {
  status: TaskRunRecord["status"]
  reduceMotion: boolean | null
}) {
  if (status === "running") {
    return (
      <span className="relative flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
        <span className="absolute inset-0 animate-ping rounded-full border border-primary/30 [animation-duration:1.8s] motion-reduce:animate-none" />
        <SearchIcon className="relative size-3.5" />
      </span>
    )
  }

  if (status === "queued") {
    return (
      <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
        <CircleDashedIcon className="size-4 animate-spin [animation-duration:3s] motion-reduce:animate-none" />
      </span>
    )
  }

  if (status === "succeeded") {
    return (
      <motion.span
        initial={reduceMotion ? false : { scale: 0.7, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        className="flex size-7 shrink-0 items-center justify-center rounded-full bg-emerald-500/10 text-emerald-500"
      >
        <CheckIcon className="size-3.5" strokeWidth={2.5} />
      </motion.span>
    )
  }

  return (
    <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-destructive/10 text-destructive">
      <CircleXIcon className="size-3.5" />
    </span>
  )
}

function getMetadataNumber(
  event: ProgressEvent | undefined,
  key: string
): number | null {
  const value = event?.metadata?.[key]
  return typeof value === "number" ? value : null
}

function getSavedResultCount(run: TaskRunRecord): number | null {
  const results = run.output_json?.results
  return Array.isArray(results) ? results.length : null
}

function terminalMessage(run: TaskRunRecord) {
  if (run.status === "succeeded") return "Ready to explore"
  if (run.status === "cancelled") return "Search cancelled"
  if (run.status === "skipped") return "Search skipped"
  return run.error ?? "Search failed"
}

function formatElapsed(seconds: number) {
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  const remainingSeconds = seconds % 60
  return `${minutes}m ${remainingSeconds.toString().padStart(2, "0")}s`
}
