import { useState } from "react"
import { ExternalLinkIcon, LoaderCircleIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
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
  useGraphRunFailures,
  useGraphRunMaterializationLag,
  useGraphRuns,
} from "@/hooks/use-crawl-graphs"
import type {
  CrawlConcurrencyLimits,
  GraphRunMaterializationLag,
  GraphRunRecord,
} from "@/types/graphs"

export function CrawlMetricsPage() {
  const runsQuery = useGraphRuns()
  const capacityQuery = useCrawlConcurrencyLimits()
  const materializationLagQuery = useGraphRunMaterializationLag()

  if (
    runsQuery.isLoading ||
    capacityQuery.isLoading ||
    materializationLagQuery.isLoading
  ) {
    return (
      <LoaderCircleIcon className="m-auto size-5 animate-spin text-muted-foreground" />
    )
  }

  const runs = runsQuery.data?.items ?? []
  const lag = materializationLagQuery.data?.items ?? []
  const lagByRun = new Map(lag.map((item) => [item.run_id, item]))
  const acquisitionBacklog = runs.reduce(
    (total, run) => total + (run.queued_request_count ?? 0),
    0
  )
  const ingestionBacklog = runs.reduce(
    (total, run) => total + (run.processing_request_count ?? 0),
    0
  )
  const materializationBacklog = lag.reduce(
    (total, item) => total + item.pending_updates,
    0
  )

  return (
    <div className="flex w-full min-w-0 flex-col gap-4 pb-2">
      <WorkerCapacity
        capacity={capacityQuery.data}
        acquisitionBacklog={acquisitionBacklog}
        ingestionBacklog={ingestionBacklog}
        materializationBacklog={materializationBacklog}
      />
      <SharedPressure capacity={capacityQuery.data} />
      <LatestRuns runs={runs} lagByRun={lagByRun} />
    </div>
  )
}

function WorkerCapacity({
  capacity,
  acquisitionBacklog,
  ingestionBacklog,
  materializationBacklog,
}: {
  capacity?: CrawlConcurrencyLimits
  acquisitionBacklog: number
  ingestionBacklog: number
  materializationBacklog: number
}) {
  const ingestion = capacity?.catalogue_executors.find(
    (item) => item.capability === "ingestion"
  )
  const materialization = capacity?.catalogue_executors.find(
    (item) => item.capability === "materialization"
  )

  return (
    <Card>
      <CardHeader>
        <CardTitle>Worker capacity</CardTitle>
        <CardDescription>
          Sustained full utilization with a growing backlog means this
          deployment needs more replicas.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3 lg:grid-cols-3">
        <WorkerCapacityCard
          label="Acquisition"
          used={capacity?.runtime_active ?? 0}
          capacity={capacity?.runtime_capacity ?? 0}
          replicas={capacity?.worker_count ?? 0}
          backlog={acquisitionBacklog}
          backlogLabel="pages queued"
        />
        <WorkerCapacityCard
          label="Ingestion"
          used={ingestion?.active ?? 0}
          capacity={ingestion?.capacity ?? 0}
          replicas={ingestion?.worker_count ?? 0}
          backlog={ingestionBacklog}
          backlogLabel="pages awaiting ingestion / graph"
        />
        <WorkerCapacityCard
          label="Materialization"
          used={materialization?.active ?? 0}
          capacity={materialization?.capacity ?? 0}
          replicas={materialization?.worker_count ?? 0}
          backlog={materializationBacklog}
          backlogLabel="view updates waiting"
        />
      </CardContent>
    </Card>
  )
}

function WorkerCapacityCard({
  label,
  used,
  capacity,
  replicas,
  backlog,
  backlogLabel,
}: {
  label: string
  used: number
  capacity: number
  replicas: number
  backlog: number
  backlogLabel: string
}) {
  const percent = capacity > 0 ? Math.min(100, (used / capacity) * 100) : 0
  const full = capacity > 0 && used >= capacity
  const needsScale = full && backlog > 0
  const unavailable = capacity === 0

  return (
    <div className="rounded-lg border bg-muted/10 p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-medium">{label}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {replicas.toLocaleString()} healthy{" "}
            {replicas === 1 ? "replica" : "replicas"}
          </p>
        </div>
        <Badge
          variant={
            unavailable || needsScale
              ? "destructive"
              : full
                ? "secondary"
                : "outline"
          }
        >
          {unavailable
            ? "Unavailable"
            : needsScale
              ? "Scale replicas"
              : full
                ? "At capacity"
                : "Available"}
        </Badge>
      </div>
      <p
        className={`mt-5 text-3xl font-semibold tabular-nums ${unavailable || needsScale ? "text-destructive" : ""}`}
      >
        {used.toLocaleString()} / {capacity.toLocaleString()}
      </p>
      <p className="mt-0.5 text-xs text-muted-foreground">
        active / total slots
      </p>
      <div
        className="mt-3 h-2 overflow-hidden rounded-full bg-muted"
        role="progressbar"
        aria-label={`${label} slot utilization`}
        aria-valuemin={0}
        aria-valuemax={capacity}
        aria-valuenow={used}
      >
        <div
          className={`h-full rounded-full transition-[width] duration-700 ease-out motion-reduce:transition-none ${unavailable || needsScale ? "bg-destructive" : "bg-primary"}`}
          style={{ width: `${percent}%` }}
        />
      </div>
      <p
        className={`mt-3 text-sm tabular-nums ${backlog > 0 ? "font-medium" : "text-muted-foreground"}`}
      >
        {backlog.toLocaleString()} {backlogLabel}
      </p>
    </div>
  )
}

function SharedPressure({ capacity }: { capacity?: CrawlConcurrencyLimits }) {
  const waiting = (capacity?.resources ?? []).filter(
    (resource) =>
      resource.waiting > 0 &&
      (resource.name === "catalogue:hot" || resource.name.startsWith("object:"))
  )
  if (waiting.length === 0) return null

  return (
    <Card className="border-amber-500/40 bg-amber-500/5">
      <CardHeader>
        <CardTitle>Shared infrastructure is limiting work</CardTitle>
        <CardDescription>
          Adding worker replicas will not remove this wait; inspect the
          corresponding catalogue or object-store budget.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {waiting.map((resource) => (
          <div
            key={resource.name}
            className="rounded-md border bg-background/60 p-3"
          >
            <div className="flex items-center justify-between gap-4">
              <span className="text-sm font-medium">
                {resourceLabel(resource.name)}
              </span>
              <span className="font-medium tabular-nums">
                {resource.used} / {resource.capacity}
              </span>
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              {resource.waiting.toLocaleString()} waiting · oldest{" "}
              {formatAge(resource.oldest_wait_seconds * 1_000)}
            </p>
          </div>
        ))}
      </CardContent>
    </Card>
  )
}

function LatestRuns({
  runs,
  lagByRun,
}: {
  runs: GraphRunRecord[]
  lagByRun: Map<string, GraphRunMaterializationLag>
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Latest runs</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto rounded-md border">
          <Table className="min-w-[64rem]">
            <TableHeader className="bg-muted/30">
              <TableRow className="hover:bg-transparent">
                <TableHead className="w-[8rem] pl-4">Status</TableHead>
                <TableHead className="w-[15rem]">Graph</TableHead>
                <TableHead>Pages</TableHead>
                <TableHead className="w-[6rem] text-right">Errors</TableHead>
                <TableHead className="w-[14rem] pr-4">View updates</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {runs.map((run) => (
                <RunRow key={run.id} run={run} lag={lagByRun.get(run.id)} />
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

function RunRow({
  run,
  lag,
}: {
  run: GraphRunRecord
  lag?: GraphRunMaterializationLag
}) {
  const progress = describeRunProgress(run)
  const coolingDown = isTerminalRun(run) && Boolean(lag?.pending_updates)
  const unhealthy = run.status === "failed" || progress.stalled
  const errorCount = run.error_count + (lag?.failed_updates ?? 0)

  return (
    <TableRow title={`Run ${run.id}`}>
      <TableCell className="py-4 pl-4 align-top whitespace-normal">
        <Badge
          variant={
            unhealthy ? "destructive" : coolingDown ? "secondary" : "outline"
          }
        >
          {progress.stalled
            ? "Stalled"
            : coolingDown
              ? "Cooldown"
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
            {(run.queued_request_count ?? 0).toLocaleString()} queued ·{" "}
            {(run.fetching_request_count ?? 0).toLocaleString()} acquiring ·{" "}
            {(run.processing_request_count ?? 0).toLocaleString()} ingesting /
            graph
          </p>
        ) : null}
      </TableCell>
      <RunErrors run={run} lag={lag} errorCount={errorCount} />
      <TableCell className="py-4 pr-4 align-top whitespace-normal">
        <MaterializationLag lag={lag} />
      </TableCell>
    </TableRow>
  )
}

function RunErrors({
  run,
  lag,
  errorCount,
}: {
  run: GraphRunRecord
  lag?: GraphRunMaterializationLag
  errorCount: number
}) {
  const [open, setOpen] = useState(false)
  const failures = useGraphRunFailures(run.id, open && run.error_count > 0)

  if (errorCount === 0) {
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
          {errorCount.toLocaleString()}
        </DialogTrigger>
        <DialogContent className="max-h-[85svh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>Run errors</DialogTitle>
            <DialogDescription>
              Crawl and view-update failures recorded for run {run.id}.
            </DialogDescription>
          </DialogHeader>
          {run.error ? (
            <ErrorDetail label="Run failure" detail={run.error} />
          ) : null}
          {failures.isLoading ? (
            <LoaderCircleIcon className="mx-auto my-6 size-5 animate-spin text-muted-foreground" />
          ) : failures.isError ? (
            <p className="text-sm text-destructive">
              Failed to load crawl error details.
            </p>
          ) : (
            failures.data?.items.map((failure) => (
              <div
                key={failure.crawl_id}
                className="rounded-md border bg-muted/20 p-3 text-left"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="destructive">
                    {failure.failure_code ?? "crawl_failed"}
                  </Badge>
                  <span className="text-xs text-muted-foreground">
                    {failure.failure_stage ?? "unknown stage"}
                    {failure.status_code
                      ? ` · HTTP ${failure.status_code}`
                      : ""}
                    {` · ${new Date(failure.captured_at).toLocaleString()}`}
                  </span>
                </div>
                <p className="mt-2 text-sm font-medium break-all">
                  {failure.requested_url}
                </p>
                {failure.failure_detail ? (
                  <p className="mt-2 text-sm whitespace-pre-wrap text-muted-foreground">
                    {failure.failure_detail}
                  </p>
                ) : null}
              </div>
            ))
          )}
          {lag?.failed_updates ? (
            <ErrorDetail
              label="View updates"
              detail={`${lag.failed_updates.toLocaleString()} failed materialization updates`}
            />
          ) : null}
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

function MaterializationLag({ lag }: { lag?: GraphRunMaterializationLag }) {
  if (!lag || lag.materialization_count === 0) {
    return <span className="text-muted-foreground">None</span>
  }
  if (lag.pending_updates === 0 && lag.failed_updates === 0) {
    return <span className="font-medium">Up to date</span>
  }
  return (
    <div>
      <p className="font-medium tabular-nums">
        {lag.pending_updates.toLocaleString()} waiting
      </p>
      <p className="mt-1 text-xs text-muted-foreground tabular-nums">
        {lag.materialization_count.toLocaleString()}{" "}
        {lag.materialization_count === 1 ? "view" : "views"}
        {lag.failed_updates
          ? ` · ${lag.failed_updates.toLocaleString()} failed`
          : ""}
      </p>
    </div>
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

function resourceLabel(name: string) {
  if (name === "catalogue:hot") return "Catalogue"
  if (name === "object:read") return "Object reads"
  if (name === "object:write") return "Object writes"
  return name
}

function isActiveRun(run: GraphRunRecord) {
  return run.status === "queued" || run.status === "running"
}

function isTerminalRun(run: GraphRunRecord) {
  return !isActiveRun(run)
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
