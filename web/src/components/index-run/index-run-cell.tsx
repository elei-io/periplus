import {
  ChevronDownIcon,
  ChevronRightIcon,
  DatabaseIcon,
  XIcon,
} from "lucide-react"
import { AnimatePresence, motion, useReducedMotion } from "motion/react"
import { useEffect, useState } from "react"

import { IndexResults } from "@/components/index-run/index-results"
import { PlaygroundRunStatusMark } from "@/components/playground-run-status-mark"
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
import { useTaskRunProgress, useTaskRunResult } from "@/hooks/use-action-runs"
import { useCancelIndexRun } from "@/hooks/use-index-runs"
import { truncateMiddle } from "@/lib/truncate"
import { cn } from "@/lib/utils"
import type { IndexInput, IndexLink } from "@/types/index"
import type { ProgressEvent } from "@/types/progress"
import type { TaskRunRecord } from "@/types/tasks"

const activeStatuses = new Set(["queued", "running"])

type IndexRunCellProps = {
  run: TaskRunRecord
  density: "live" | "recent"
}

export function IndexRunCell({ run, density }: IndexRunCellProps) {
  const [expanded, setExpanded] = useState(false)
  const [now, setNow] = useState(() => Date.now())
  const cancelMutation = useCancelIndexRun()
  const reduceMotion = useReducedMotion()
  const active = activeStatuses.has(run.status)
  const events = useTaskRunProgress("index", run.id, active)
  const input = run.input_json as IndexInput
  const latest = events.at(-1)

  useEffect(() => {
    if (!active) return undefined
    const timer = window.setInterval(() => setNow(Date.now()), 1_000)
    return () => window.clearInterval(timer)
  }, [active])

  const resultQuery = useTaskRunResult<IndexLink[]>(
    run.id,
    expanded && run.status === "succeeded"
  )

  const latestText = latest
    ? [latest.message, latest.resource ? truncateMiddle(latest.resource) : null]
        .filter(Boolean)
        .join(" · ")
    : run.status === "queued"
      ? "Waiting for a worker…"
      : active
        ? "Getting things moving…"
        : terminalMessage(run)
  const depthProgressEvent = events.findLast(
    (event) => event.phase === "index_depth"
  )
  const latestBatchEventIndex = events.findLastIndex(
    (event) => event.phase === "crawl_batch"
  )
  const latestBatchEvent =
    latestBatchEventIndex >= 0 ? events[latestBatchEventIndex] : undefined
  const batchProgressEvent =
    latestBatchEvent?.status === "started" ? latestBatchEvent : undefined
  const batchTotal = batchProgressEvent?.total ?? null
  const pagesCompletedSinceCheckpoint = batchProgressEvent
    ? new Set(
        events
          .slice(latestBatchEventIndex + 1)
          .filter(
            (event) =>
              event.phase === "crawl" &&
              (event.status === "succeeded" || event.status === "failed")
          )
          .map((event) => event.operation_id)
      ).size
    : 0
  const batchCurrent = batchProgressEvent
    ? Math.min(
        batchTotal ?? Number.POSITIVE_INFINITY,
        (batchProgressEvent.current ?? 0) + pagesCompletedSinceCheckpoint
      )
    : null
  const progressValue =
    batchCurrent !== null && batchCurrent > 0 && batchTotal
      ? Math.min(100, (batchCurrent / batchTotal) * 100)
      : depthProgressEvent?.current !== undefined && depthProgressEvent.total
        ? Math.min(
            100,
            (depthProgressEvent.current / depthProgressEvent.total) * 100
          )
        : null
  const liveLinkCount = getMetadataNumber(
    depthProgressEvent,
    "discovered_links"
  )
  const savedLinkCount = getSavedLinkCount(run)
  const linkCount = liveLinkCount ?? savedLinkCount
  const elapsedFrom = run.started_at ?? run.queued_at
  const elapsedTo = run.finished_at ? new Date(run.finished_at).getTime() : now
  const elapsedSeconds = Math.max(
    0,
    Math.round((elapsedTo - new Date(elapsedFrom).getTime()) / 1000)
  )
  const messageKey = `${latest?.operation_id ?? run.status}-${latest?.status ?? "idle"}-${latest?.message ?? ""}-${latest?.current ?? ""}`

  const rowContent = (
    <>
      <PlaygroundRunStatusMark
        status={run.status}
        activeIcon={<DatabaseIcon className="relative size-3.5" />}
      />
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate text-sm font-medium" title={input.url}>
            {input.url}
          </span>
          {active && batchCurrent !== null && batchCurrent > 0 && batchTotal ? (
            <span className="shrink-0 rounded-full bg-link/10 px-1.5 py-0.5 text-[10px] font-medium text-link tabular-nums">
              {batchCurrent}/{batchTotal} pages
            </span>
          ) : active && linkCount !== null && linkCount > 0 ? (
            <span className="shrink-0 rounded-full bg-link/10 px-1.5 py-0.5 text-[10px] font-medium text-link tabular-nums">
              {linkCount} found
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
          Depth {input.max_depth}
          {input.dedupe ? " · dedupe" : ""}
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
                aria-label={`Cancel index for ${input.url}`}
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
          {linkCount !== null ? (
            <span className="hidden tabular-nums md:inline">
              {linkCount} {linkCount === 1 ? "link" : "links"}
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
      ? "playground-run-live min-h-[4.5rem] py-3 hover:bg-muted/25"
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
        <div className="absolute inset-x-0 bottom-0 h-px overflow-hidden bg-link/10">
          {progressValue === null ? (
            <div className="playground-run-progress-indeterminate h-full w-1/3 bg-link/70" />
          ) : (
            <motion.div
              className="h-full bg-link/80"
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
            <p className="text-sm text-muted-foreground">Loading links…</p>
          ) : resultQuery.isError ? (
            <p className="text-sm text-destructive">
              {resultQuery.error.message}
            </p>
          ) : (
            <IndexResults links={resultQuery.data ?? []} />
          )}
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}

function getMetadataNumber(
  event: ProgressEvent | undefined,
  key: string
): number | null {
  const value = event?.metadata?.[key]
  return typeof value === "number" ? value : null
}

function getSavedLinkCount(run: TaskRunRecord): number | null {
  const links = run.output_json?.links
  return Array.isArray(links) ? links.length : null
}

function terminalMessage(run: TaskRunRecord) {
  if (run.status === "succeeded") return "Ready to explore"
  if (run.status === "cancelled") return "Index cancelled"
  if (run.status === "skipped") return "Index skipped"
  return run.error ?? "Index failed"
}

function formatElapsed(seconds: number) {
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  const remainingSeconds = seconds % 60
  return `${minutes}m ${remainingSeconds.toString().padStart(2, "0")}s`
}
