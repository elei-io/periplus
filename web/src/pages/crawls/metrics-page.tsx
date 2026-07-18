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
  const acquisitionBacklog = runs.reduce(
    (total, run) => total + run.queued_request_count,
    0
  )
  const ingestionBacklog =
    capacityQuery.data?.catalogue_executors.find(
      (item) => item.capability === "ingestion"
    )?.backlog ?? 0
  const materializationBacklog =
    capacityQuery.data?.catalogue_executors.find(
      (item) => item.capability === "materialization"
    )?.backlog ?? 0

  return (
    <div className="flex w-full min-w-0 flex-col gap-4 pb-2">
      <WorkerCapacity
        capacity={capacityQuery.data}
        acquisitionBacklog={acquisitionBacklog}
        ingestionBacklog={ingestionBacklog}
        materializationBacklog={materializationBacklog}
      />
      <SharedPressure capacity={capacityQuery.data} />
      <DomainPoliteness capacity={capacityQuery.data} />
      <LatestRuns runs={runs} />
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
          backlogLabel="catalogue jobs waiting"
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

function DomainPoliteness({ capacity }: { capacity?: CrawlConcurrencyLimits }) {
  const domains = (capacity?.resources ?? [])
    .filter(
      (resource) =>
        resource.name.startsWith("remote:") &&
        (resource.used > 0 || resource.waiting > 0)
    )
    .sort(
      (left, right) =>
        Number(right.waiting > 0) - Number(left.waiting > 0) ||
        right.used / right.capacity - left.used / left.capacity ||
        domainName(left.name).localeCompare(domainName(right.name))
    )

  if (domains.length === 0) return null

  return (
    <Card>
      <CardHeader>
        <CardTitle>Live domain politeness</CardTitle>
        <CardDescription>
          Current page acquisitions governed by each domain&apos;s maximum
          concurrency. Limits disappear after their permits and waiters drain.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto rounded-md border">
          <Table>
            <TableHeader className="bg-muted/30">
              <TableRow className="hover:bg-transparent">
                <TableHead className="pl-4">Domain</TableHead>
                <TableHead className="w-[12rem]">Concurrency</TableHead>
                <TableHead className="w-[10rem] pr-4 text-right">
                  Waiting
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {domains.map((resource) => (
                <TableRow key={resource.name}>
                  <TableCell className="py-3 pl-4 font-medium">
                    {domainName(resource.name)}
                  </TableCell>
                  <TableCell className="py-3 tabular-nums">
                    {resource.used.toLocaleString()} /{" "}
                    {resource.capacity.toLocaleString()} active
                  </TableCell>
                  <TableCell className="py-3 pr-4 text-right tabular-nums">
                    {resource.waiting > 0 ? (
                      <span className="font-medium text-amber-700 dark:text-amber-400">
                        {resource.waiting.toLocaleString()}
                        {resource.oldest_wait_seconds > 0
                          ? ` · oldest ${formatAge(resource.oldest_wait_seconds * 1_000)}`
                          : ""}
                      </span>
                    ) : (
                      <span className="text-muted-foreground">0</span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
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
  const failures = useGraphRunFailures(run.id, open && run.error_count > 0)

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
              Crawl failures recorded for run {run.id}.
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

function resourceLabel(name: string) {
  if (name === "catalogue:hot") return "Catalogue"
  if (name === "object:read") return "Object reads"
  if (name === "object:write") return "Object writes"
  return name
}

function domainName(resourceName: string) {
  return resourceName.slice("remote:".length)
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
