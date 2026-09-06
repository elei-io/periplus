import {
  CheckIcon,
  LoaderCircleIcon,
  PauseIcon,
  TriangleAlertIcon,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import {
  GraphProgressProvider,
  useGraphProgressSummary,
} from "@/hooks/use-graph-progress"
import { useGraphRun } from "@/hooks/use-crawl-graphs"
import type { GraphRunDetail } from "@/types/graphs"

export function ChatCrawlProgress({
  runId,
  planSlug,
  startUrlCount,
}: {
  runId: string
  planSlug: string
  startUrlCount: number
}) {
  return (
    <GraphProgressProvider runId={runId}>
      <ChatCrawlProgressContent
        runId={runId}
        planSlug={planSlug}
        startUrlCount={startUrlCount}
      />
    </GraphProgressProvider>
  )
}

function ChatCrawlProgressContent({
  runId,
  planSlug,
  startUrlCount,
}: {
  runId: string
  planSlug: string
  startUrlCount: number
}) {
  const runQuery = useGraphRun(runId)
  const progress = useGraphProgressSummary(runId)
  const run = runQuery.data

  if (runQuery.isError) {
    return (
      <div className="border-t px-3 py-3 text-xs text-destructive">
        Crawl progress is unavailable for run {runId.slice(0, 8)}.
      </div>
    )
  }

  const status = crawlStatus(run)
  const total = Math.max(run?.request_count ?? 0, progress.admitted)
  const reportedSettled = Math.max(
    0,
    (run?.request_count ?? 0) - (run?.pending_request_count ?? 0)
  )
  const settled = Math.max(
    reportedSettled,
    progress.completed + progress.failed + progress.cancelled
  )
  const percent =
    status.terminal && total > 0
      ? 100
      : total > 0
        ? Math.min(100, (settled / total) * 100)
        : 0

  return (
    <div
      className="border-t bg-muted/10 px-3 py-3"
      aria-live="polite"
      aria-label={`Crawl ${status.label.toLowerCase()}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <CrawlStatusIcon run={run} />
          <span className="font-medium">{status.label}</span>
          <Badge variant="outline" className="max-w-48 truncate font-mono">
            {planSlug}
          </Badge>
        </div>
        <a
          href={`/crawls/metrics?run=${runId}`}
          className="text-[0.6875rem] text-muted-foreground hover:text-foreground hover:underline"
        >
          Run {runId.slice(0, 8)}
        </a>
      </div>

      <div className="mt-2 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 text-xs">
        <p className="tabular-nums">
          <span className="font-medium">{settled.toLocaleString()}</span>
          <span className="text-muted-foreground">
            {" "}
            / {total.toLocaleString()} pages settled
          </span>
        </p>
        <p className="text-muted-foreground tabular-nums">
          {startUrlCount.toLocaleString()}{" "}
          {startUrlCount === 1 ? "start URL" : "start URLs"}
          {run?.crawl_limit_reached ? " · page limit reached" : ""}
        </p>
      </div>

      <div
        className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted"
        role="progressbar"
        aria-label="Crawl pages settled"
        aria-valuemin={0}
        aria-valuemax={Math.max(1, total)}
        aria-valuenow={settled}
      >
        <div
          className={`h-full rounded-full transition-[width] duration-500 ${
            status.error ? "bg-destructive" : "bg-primary"
          }`}
          style={{ width: `${percent}%` }}
        />
      </div>

      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[0.6875rem] text-muted-foreground tabular-nums">
        {progress.queued > 0 ? (
          <span>{progress.queued.toLocaleString()} queued</span>
        ) : null}
        {progress.crawling > 0 ? (
          <span>{progress.crawling.toLocaleString()} acquiring</span>
        ) : null}
        {progress.navigating > 0 ? (
          <span>{progress.navigating.toLocaleString()} following links</span>
        ) : null}
        {(run?.failed_request_count ?? progress.failed) > 0 ? (
          <span className="text-destructive">
            {(run?.failed_request_count ?? progress.failed).toLocaleString()}{" "}
            failed
          </span>
        ) : null}
        {!status.terminal &&
        progress.queued === 0 &&
        progress.crawling === 0 &&
        progress.navigating === 0 ? (
          <span>
            {progress.connectionState === "connected"
              ? "Preparing pages…"
              : "Connecting to crawl progress…"}
          </span>
        ) : null}
        {status.terminal && !status.error ? (
          <span>
            {status.label === "Completed"
              ? "All crawl work settled."
              : "Crawl work stopped."}
          </span>
        ) : null}
      </div>

      {run?.error ? (
        <p className="mt-2 text-xs text-destructive">{run.error}</p>
      ) : null}
    </div>
  )
}

function CrawlStatusIcon({ run }: { run?: GraphRunDetail }) {
  if (!run || run.status === "queued" || run.status === "running") {
    return <LoaderCircleIcon className="size-3.5 animate-spin text-primary" />
  }
  if (run.status === "paused") {
    return <PauseIcon className="size-3.5 text-muted-foreground" />
  }
  if (run.status === "completed") {
    return <CheckIcon className="size-3.5 text-emerald-500" />
  }
  return <TriangleAlertIcon className="size-3.5 text-destructive" />
}

function crawlStatus(run?: GraphRunDetail) {
  switch (run?.status) {
    case "paused":
      return { label: "Crawl paused", terminal: false, error: false }
    case "completed":
      return { label: "Completed", terminal: true, error: false }
    case "completed_with_errors":
      return { label: "Completed with errors", terminal: true, error: true }
    case "failed":
      return { label: "Crawl failed", terminal: true, error: true }
    case "cancelled":
      return { label: "Crawl cancelled", terminal: true, error: true }
    case "queued":
      return { label: "Crawl queued", terminal: false, error: false }
    default:
      return { label: "Crawling", terminal: false, error: false }
  }
}
