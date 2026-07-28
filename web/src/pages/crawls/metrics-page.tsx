import { useState } from "react"
import {
  ChevronDownIcon,
  ExternalLinkIcon,
  LoaderCircleIcon,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  useCrawlConcurrencyLimits,
  useGraphRunFailureSummary,
  useGraphRuns,
} from "@/hooks/use-crawl-graphs"
import { extractApiError } from "@/lib/api"
import type { CrawlConcurrencyLimits, GraphRunRecord } from "@/types/graphs"

export function CrawlMetricsPage() {
  const runsQuery = useGraphRuns()
  const capacityQuery = useCrawlConcurrencyLimits()
  const error = runsQuery.error ?? capacityQuery.error

  if (runsQuery.isLoading || capacityQuery.isLoading) {
    return (
      <LoaderCircleIcon className="m-auto size-5 animate-spin text-muted-foreground" />
    )
  }
  if (error) {
    return (
      <Card className="border-destructive/40">
        <CardHeader>
          <CardTitle>Metrics are unavailable</CardTitle>
          <CardDescription>{extractApiError(error)}</CardDescription>
        </CardHeader>
      </Card>
    )
  }

  const runs = runsQuery.data?.items ?? []
  const queued = runs.reduce(
    (total, run) => total + run.queued_request_count,
    0
  )
  const acquiring = runs.reduce(
    (total, run) => total + run.fetching_request_count,
    0
  )
  const navigating = runs.reduce(
    (total, run) => total + run.navigating_request_count,
    0
  )

  return (
    <div className="flex w-full min-w-0 flex-col gap-4 pb-2">
      <AcquisitionStatus
        capacity={capacityQuery.data}
        queued={queued}
        acquiring={acquiring}
        navigating={navigating}
      />
      <LatestRuns runs={runs} />
      <AcquisitionDiagnostics capacity={capacityQuery.data} queued={queued} />
    </div>
  )
}

function AcquisitionStatus({
  capacity,
  queued,
  acquiring,
  navigating,
}: {
  capacity?: CrawlConcurrencyLimits
  queued: number
  acquiring: number
  navigating: number
}) {
  const unavailable = (capacity?.runtime_capacity ?? 0) === 0
  const active = acquiring + navigating
  const state = unavailable
    ? "Unavailable"
    : queued > 0 || active > 0
      ? "Acquiring"
      : "Idle"

  return (
    <Card>
      <CardHeader className="border-b">
        <CardTitle>Acquisition status</CardTitle>
        <CardDescription>
          Pages moving through crawl graphs right now.
        </CardDescription>
        <CardAction>
          <Badge variant={unavailable ? "destructive" : "outline"}>
            {state}
          </Badge>
        </CardAction>
      </CardHeader>
      <CardContent className="grid gap-6 sm:grid-cols-3">
        <StatusCount value={queued} label="pages queued" />
        <StatusCount value={acquiring} label="pages acquiring" />
        <StatusCount value={navigating} label="navigating the graph" />
      </CardContent>
    </Card>
  )
}

function StatusCount({ value, label }: { value: number; label: string }) {
  return (
    <div>
      <p className="text-3xl font-semibold tabular-nums">
        {value.toLocaleString()}
      </p>
      <p className="mt-1 text-sm text-muted-foreground">{label}</p>
    </div>
  )
}

function AcquisitionDiagnostics({
  capacity,
  queued,
}: {
  capacity?: CrawlConcurrencyLimits
  queued: number
}) {
  const used = capacity?.runtime_active ?? 0
  const usable = capacity?.runtime_capacity ?? 0
  const configured = (capacity?.workers ?? []).reduce(
    (total, worker) => total + worker.capacity,
    0
  )

  return (
    <Collapsible>
      <Card>
        <CardHeader>
          <CardTitle>Operations diagnostics</CardTitle>
          <CardDescription>
            Acquisition worker capacity and queue detail.
          </CardDescription>
          <CardAction>
            <CollapsibleTrigger className="group inline-flex items-center gap-2 rounded-md px-2 py-1 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground">
              Details
              <ChevronDownIcon className="size-3.5 transition-transform group-data-[panel-open]:rotate-180" />
            </CollapsibleTrigger>
          </CardAction>
        </CardHeader>
        <CollapsibleContent>
          <CardContent className="grid gap-3 border-t pt-4 sm:grid-cols-2 lg:grid-cols-4">
            <DiagnosticValue
              value={`${used.toLocaleString()} / ${usable.toLocaleString()}`}
              label="active / usable slots"
            />
            <DiagnosticValue
              value={configured.toLocaleString()}
              label="configured slots"
            />
            <DiagnosticValue
              value={(capacity?.worker_count ?? 0).toLocaleString()}
              label="worker replicas"
            />
            <DiagnosticValue
              value={queued.toLocaleString()}
              label="queued page requests"
            />
          </CardContent>
        </CollapsibleContent>
      </Card>
    </Collapsible>
  )
}

function DiagnosticValue({ value, label }: { value: string; label: string }) {
  return (
    <div className="rounded-md border bg-muted/10 p-3">
      <p className="font-medium tabular-nums">{value}</p>
      <p className="mt-1 text-xs text-muted-foreground">{label}</p>
    </div>
  )
}

function LatestRuns({ runs }: { runs: GraphRunRecord[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Latest runs</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto rounded-md border">
          <Table className="min-w-[50rem]">
            <TableHeader className="bg-muted/30">
              <TableRow className="hover:bg-transparent">
                <TableHead className="w-[8rem] pl-4">Status</TableHead>
                <TableHead className="w-[15rem]">Graph</TableHead>
                <TableHead>Pages</TableHead>
                <TableHead className="w-[6rem] text-right">Errors</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {runs.map((run) => (
                <RunRow key={run.id} run={run} />
              ))}
            </TableBody>
          </Table>
          {runs.length === 0 ? (
            <p className="py-10 text-center text-sm text-muted-foreground">
              No graph runs yet
            </p>
          ) : null}
        </div>
      </CardContent>
    </Card>
  )
}

function RunRow({ run }: { run: GraphRunRecord }) {
  const progress = describeRunProgress(run)
  const unhealthy = run.status === "failed" || progress.stalled

  return (
    <TableRow title={`Run ${run.id}`}>
      <TableCell className="py-4 pl-4 align-top whitespace-normal">
        <Badge variant={unhealthy ? "destructive" : "outline"}>
          {progress.stalled
            ? "Stalled"
            : run.status === "completed_with_errors"
              ? "Completed"
              : sentenceCase(run.status)}
        </Badge>
      </TableCell>
      <TableCell className="py-4 align-top whitespace-normal">
        <a
          href={`/crawls/graphs/${run.graph_id}`}
          className="inline-flex max-w-full items-center gap-1 font-medium hover:underline"
        >
          <span className="truncate">{run.graph_slug || "Deleted graph"}</span>
          <ExternalLinkIcon className="size-3 shrink-0 text-muted-foreground" />
        </a>
        <p className="mt-1 text-xs text-muted-foreground tabular-nums">
          {run.started_at
            ? `Started ${relativeTime(run.started_at)}`
            : `Queued ${relativeTime(run.created_at)}`}
        </p>
        <p className="mt-1 text-xs text-muted-foreground tabular-nums">
          Budget {run.request_count.toLocaleString()} /{" "}
          {run.max_crawls.toLocaleString()}
          {run.crawl_limit_reached ? " · limit reached" : ""}
        </p>
      </TableCell>
      <TableCell className="py-4 align-top whitespace-normal">
        <div className="flex items-center justify-between gap-4">
          <span className="font-medium tabular-nums">
            {progress.settled.toLocaleString()} /{" "}
            {run.request_count.toLocaleString()}
          </span>
          <span className="text-xs text-muted-foreground tabular-nums">
            {progress.activityLabel}
          </span>
        </div>
        <RunProgress run={run} className="mt-2" />
        {isActiveRun(run) && run.pending_request_count > 0 ? (
          <p className="mt-1.5 text-xs text-muted-foreground tabular-nums">
            {run.queued_request_count.toLocaleString()} queued ·{" "}
            {run.fetching_request_count.toLocaleString()} acquiring ·{" "}
            {run.navigating_request_count.toLocaleString()} navigating graph
          </p>
        ) : null}
      </TableCell>
      <RunErrors run={run} />
    </TableRow>
  )
}

function RunErrors({ run }: { run: GraphRunRecord }) {
  const [open, setOpen] = useState(false)
  const failures = useGraphRunFailureSummary(
    run.id,
    open && run.error_count > 0
  )

  if (run.error_count === 0) {
    return <CountCell value={0} />
  }

  return (
    <TableCell className="py-4 text-right align-top">
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogTrigger
          render={
            <Button
              variant="link"
              className="h-auto px-0 font-medium text-destructive"
            />
          }
        >
          {run.error_count.toLocaleString()}
        </DialogTrigger>
        <DialogContent className="max-h-[85svh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>Run errors</DialogTitle>
            <DialogDescription>
              Failure counts by type for run {run.id}.
            </DialogDescription>
          </DialogHeader>
          {run.error ? (
            <ErrorDetail label="Run failure" detail={run.error} />
          ) : null}
          {failures.isLoading ? (
            <LoaderCircleIcon className="mx-auto my-6 size-5 animate-spin text-muted-foreground" />
          ) : failures.isError ? (
            <p className="text-sm text-destructive">
              Failed to load the crawl failure summary.
            </p>
          ) : failures.data?.items.length ? (
            failures.data?.items.map((failure) => (
              <div
                key={`${failure.failure_stage}:${failure.failure_code}:${failure.status_code ?? "none"}`}
                className="rounded-md border bg-muted/20 p-3 text-left"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="destructive">{failure.failure_code}</Badge>
                  <Badge variant="outline" className="tabular-nums">
                    {failure.count.toLocaleString()}
                  </Badge>
                  <span className="text-xs text-muted-foreground">
                    {failure.failure_stage}
                    {failure.status_code
                      ? ` · HTTP ${failure.status_code}`
                      : ""}
                    {` · latest ${new Date(failure.last_occurred_at).toLocaleString()}`}
                  </span>
                </div>
                <p className="mt-2 text-sm font-medium break-all">
                  {failure.example_url}
                </p>
                {failure.example_detail ? (
                  <p className="mt-2 text-sm whitespace-pre-wrap text-muted-foreground">
                    {failure.example_detail}
                  </p>
                ) : null}
              </div>
            ))
          ) : (
            <p className="text-sm text-muted-foreground">
              No grouped crawl failures are available for this run.
            </p>
          )}
        </DialogContent>
      </Dialog>
    </TableCell>
  )
}

function ErrorDetail({ label, detail }: { label: string; detail: string }) {
  return (
    <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-left">
      <p className="text-xs font-medium text-destructive">{label}</p>
      <p className="mt-1 text-sm whitespace-pre-wrap">{detail}</p>
    </div>
  )
}

function CountCell({ value }: { value: number }) {
  return (
    <TableCell className="py-4 text-right align-top">
      <span
        className={`font-medium tabular-nums ${value > 0 ? "text-destructive" : "text-muted-foreground"}`}
      >
        {value.toLocaleString()}
      </span>
    </TableCell>
  )
}

function RunProgress({
  run,
  className = "",
}: {
  run: GraphRunRecord
  className?: string
}) {
  const progress = describeRunProgress(run)
  const percent = run.request_count
    ? Math.min(100, (progress.settled / run.request_count) * 100)
    : 0
  return (
    <div
      className={`h-1 overflow-hidden rounded-full bg-muted ${className}`}
      role="progressbar"
      aria-label="Crawl progress"
      aria-valuemin={0}
      aria-valuemax={run.request_count}
      aria-valuenow={progress.settled}
    >
      <div
        className="h-full rounded-full bg-primary"
        style={{ width: `${percent}%` }}
      />
    </div>
  )
}

function describeRunProgress(run: GraphRunRecord) {
  const settled = Math.max(0, run.request_count - run.pending_request_count)
  const activityAt = new Date(
    run.completed_at ?? run.last_progress_at ?? run.started_at ?? run.created_at
  ).getTime()
  const idleMilliseconds = Math.max(0, Date.now() - activityAt)
  const stalled =
    isActiveRun(run) &&
    run.pending_request_count > 0 &&
    idleMilliseconds >= 5 * 60_000
  const age = formatAge(idleMilliseconds)
  return {
    settled,
    stalled,
    activityLabel: `${run.completed_at ? "Finished" : "Progress"} ${age === "just now" ? age : `${age} ago`}`,
  }
}

function isActiveRun(run: GraphRunRecord) {
  return run.status === "queued" || run.status === "running"
}

function relativeTime(value: string) {
  const age = formatAge(Math.max(0, Date.now() - new Date(value).getTime()))
  return age === "just now" ? age : `${age} ago`
}

function formatAge(milliseconds: number) {
  const seconds = Math.floor(milliseconds / 1_000)
  if (seconds < 10) return "just now"
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h`
  return `${Math.floor(hours / 24)}d`
}

function sentenceCase(value: string) {
  return value.charAt(0).toUpperCase() + value.slice(1)
}
