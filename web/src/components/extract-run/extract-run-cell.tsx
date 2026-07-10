import {
  ChevronDownIcon,
  ChevronRightIcon,
  SparklesIcon,
  XIcon,
} from "lucide-react"
import { AnimatePresence, motion, useReducedMotion } from "motion/react"
import { useEffect, useState } from "react"

import { ExtractResults } from "@/components/extract-run/extract-results"
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
import { useCancelExtractRun } from "@/hooks/use-extract-runs"
import { truncateMiddle } from "@/lib/truncate"
import { cn } from "@/lib/utils"
import type { ExtractInput, ExtractOutput } from "@/types/extract"
import type { ProgressEvent, ProgressPhase } from "@/types/progress"
import type { TaskRunRecord } from "@/types/tasks"

const activeStatuses = new Set(["queued", "running"])

type ExtractRunCellProps = {
  run: TaskRunRecord
  density: "live" | "recent"
}

export function ExtractRunCell({ run, density }: ExtractRunCellProps) {
  const [expanded, setExpanded] = useState(false)
  const [now, setNow] = useState(() => Date.now())
  const cancelMutation = useCancelExtractRun()
  const reduceMotion = useReducedMotion()
  const active = activeStatuses.has(run.status)
  const events = useTaskRunProgress("extract", run.id, active)
  const input = run.input_json as ExtractInput
  const latest = events.at(-1)

  useEffect(() => {
    if (!active) return undefined
    const timer = window.setInterval(() => setNow(Date.now()), 1_000)
    return () => window.clearInterval(timer)
  }, [active])

  const resultQuery = useTaskRunResult<ExtractOutput>(
    run.id,
    expanded && run.status === "succeeded"
  )
  const liveRecordCount = getLatestMetadataNumber(events, "records")
  const liveParamCount = getLatestMetadataNumber(events, "parameters")
  const savedRecordCount = getSavedRecordCount(resultQuery.data)
  const savedParamCount = getSavedParamCount(resultQuery.data)
  const recordCount = input.extract_data
    ? (liveRecordCount ?? savedRecordCount)
    : null
  const paramCount = input.extract_query_params
    ? (liveParamCount ?? savedParamCount)
    : null
  const latestText = latest
    ? [
        latest.message ?? phaseMessage(latest.phase, latest.status),
        latest.resource ? truncateMiddle(latest.resource) : null,
      ]
        .filter(Boolean)
        .join(" · ")
    : run.status === "queued"
      ? "Waiting for a worker…"
      : active
        ? "Getting things moving…"
        : terminalMessage(run)
  const progressValue = active ? getProgressValue(events) : null
  const elapsedFrom = run.started_at ?? run.queued_at
  const elapsedTo = run.finished_at ? new Date(run.finished_at).getTime() : now
  const elapsedSeconds = Math.max(
    0,
    Math.round((elapsedTo - new Date(elapsedFrom).getTime()) / 1000)
  )
  const messageKey = `${latest?.operation_id ?? run.status}-${latest?.status ?? "idle"}-${latest?.message ?? ""}-${latest?.current ?? ""}`
  const modeLabel = getModeLabel(input)
  const resultSummary = formatResultSummary(recordCount, paramCount)

  const rowContent = (
    <>
      <PlaygroundRunStatusMark
        status={run.status}
        activeIcon={<SparklesIcon className="relative size-3.5" />}
      />
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate text-sm font-medium" title={input.url}>
            {input.url}
          </span>
          {active && resultSummary ? (
            <span className="shrink-0 rounded-full bg-link/10 px-1.5 py-0.5 text-[10px] font-medium text-link tabular-nums">
              {resultSummary}
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
        <span>{modeLabel}</span>
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
                aria-label={`Cancel extraction for ${input.url}`}
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
          {resultSummary ? (
            <span className="hidden tabular-nums md:inline">
              {resultSummary}
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
            <p className="text-sm text-muted-foreground">Loading results…</p>
          ) : resultQuery.isError ? (
            <p className="text-sm text-destructive">
              {resultQuery.error.message}
            </p>
          ) : resultQuery.data ? (
            <ExtractResults input={input} result={resultQuery.data} />
          ) : null}
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}

function getLatestMetadataNumber(events: ProgressEvent[], key: string) {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const value = events[index].metadata?.[key]
    if (typeof value === "number") return value
  }
  return null
}

function getSavedRecordCount(output: ExtractOutput | undefined) {
  const results = output?.results
  return Array.isArray(results) ? results.length : null
}

function getSavedParamCount(output: ExtractOutput | undefined) {
  const queryParams = output?.query_params
  if (!queryParams || typeof queryParams !== "object") return null
  const params = (queryParams as Record<string, unknown>).params
  return Array.isArray(params) ? params.length : null
}

function getModeLabel(input: ExtractInput) {
  if (input.extract_data && input.extract_query_params)
    return "Records + params"
  if (input.extract_data) return "Records"
  return "Query params"
}

function formatResultSummary(
  recordCount: number | null,
  paramCount: number | null
) {
  const parts = []
  if (recordCount !== null) {
    parts.push(`${recordCount} ${recordCount === 1 ? "record" : "records"}`)
  }
  if (paramCount !== null) {
    parts.push(`${paramCount} ${paramCount === 1 ? "param" : "params"}`)
  }
  return parts.join(" · ")
}

function getProgressValue(events: ProgressEvent[]) {
  if (events.length === 0) return null
  return Math.max(...events.map(progressForEvent))
}

function progressForEvent(event: ProgressEvent) {
  const started: Partial<Record<ProgressPhase, number>> = {
    queue: 4,
    task: 8,
    extract: 10,
    crawl_capacity: 12,
    crawl: 18,
    schema: 35,
    collect_query_evidence: 40,
    generate_schema: 48,
    extract_query_params: 60,
    apply_data_schema: 70,
    regenerate_data_schema: 58,
  }
  const succeeded: Partial<Record<ProgressPhase, number>> = {
    queue: 7,
    task: 10,
    crawl: 32,
    schema: 68,
    collect_query_evidence: 54,
    generate_schema: 65,
    extract_query_params: 84,
    apply_data_schema: 90,
    regenerate_data_schema: 70,
    extract: 100,
  }
  return event.status === "succeeded"
    ? (succeeded[event.phase] ?? 12)
    : (started[event.phase] ?? 10)
}

function phaseMessage(phase: ProgressPhase, status: ProgressEvent["status"]) {
  if (status === "failed") return "This step could not finish."
  const messages: Partial<Record<ProgressPhase, string>> = {
    queue: "Waiting for a worker…",
    task: "Starting extraction…",
    crawl_capacity: "Waiting for browser capacity…",
    crawl: "Reading the page…",
    schema: "Preparing the extraction rules…",
    generate_schema: "Learning the page structure…",
    collect_query_evidence: "Looking for page controls…",
    extract_query_params: "Understanding query parameters…",
    apply_data_schema: "Turning the page into records…",
    regenerate_data_schema: "Refining the extraction rules…",
    extract: status === "succeeded" ? "Ready to explore" : "Extracting…",
  }
  return messages[phase] ?? "Working…"
}

function terminalMessage(run: TaskRunRecord) {
  if (run.status === "succeeded") return "Ready to explore"
  if (run.status === "cancelled") return "Extraction cancelled"
  if (run.status === "skipped") return "Extraction skipped"
  return run.error ?? "Extraction failed"
}

function formatElapsed(seconds: number) {
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  const remainingSeconds = seconds % 60
  return `${minutes}m ${remainingSeconds.toString().padStart(2, "0")}s`
}
