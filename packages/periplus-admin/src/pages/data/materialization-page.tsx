import { useState } from "react"
import { ArrowUpRightIcon, RefreshCwIcon } from "lucide-react"

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
  Progress,
  ProgressLabel,
  ProgressValue,
} from "@/components/ui/progress"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useDataStatus } from "@/hooks/use-data-status"
import { useMaterializationRuns } from "@/hooks/use-materialization"
import { extractApiError } from "@/lib/api"
import type { MaterializationRun } from "@/types/operations"
import { bytes } from "./storage-format"

const count = (value: number | null | undefined) =>
  value == null ? "—" : value.toLocaleString()
const date = (value: string | null | undefined) =>
  value ? new Date(value).toLocaleString() : "Not recorded"
const activeStatuses = new Set(["queued", "planning", "running", "activating"])
const phaseLabels: Record<string, string> = {
  queued: "Waiting for a planner",
  planning: "Planning source batches",
  running: "Building the hidden generation",
  activating: "Catching up and activating",
}

export function MaterializationPage() {
  const status = useDataStatus()
  const runs = useMaterializationRuns()
  const materialization = status.data?.materialization
  const workers = materialization?.workers
  const workloads = materialization?.workloads ?? []
  const queuesAvailable =
    workloads.length > 0 &&
    workloads.every((workload) => workload.queue.available)
  const pending = queuesAvailable
    ? workloads.reduce(
        (total, workload) => total + (workload.queue.pending ?? 0),
        0
      )
    : null
  const active =
    runs.data?.filter((run) => activeStatuses.has(run.status)) ?? []
  const latestCompleted = runs.data?.find((run) => run.status === "completed")
  const fetching = status.isFetching || runs.isFetching

  return (
    <div className="mx-auto flex w-full max-w-7xl flex-col gap-6 pb-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Materialization
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Derived projections, batch delivery, and complete generation
            rebuilds.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-muted-foreground">
            {status.data
              ? `Observed ${date(status.data.generated_at)}`
              : "Reading materialization state"}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={fetching}
            onClick={() => {
              void status.refetch()
              void runs.refetch()
            }}
          >
            <RefreshCwIcon className={fetching ? "animate-spin" : ""} />
            Refresh
          </Button>
        </div>
      </div>
      {status.error && (
        <ReadError
          label="Delivery and worker status"
          error={status.error}
          stale={!!status.data}
        />
      )}
      {runs.error && (
        <ReadError
          label="Rebuild history"
          error={runs.error}
          stale={!!runs.data}
        />
      )}
      {status.isPending && !status.data ? (
        <div
          aria-label="Loading materialization status"
          className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4"
        >
          {[0, 1, 2, 3].map((value) => (
            <Skeleton key={value} className="h-32" />
          ))}
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <Metric
            label="Pending batches"
            value={count(pending)}
            detail="Not yet delivered to a materializer"
          />
          <Metric
            label="Materializer processes"
            value={count(workers?.worker_count)}
            detail="Processes in the current presence reports"
          />
          <Metric
            label="Active rebuilds"
            value={runs.data ? count(active.length) : "—"}
            detail="Queued, planning, building, or activating"
          />
          <Metric
            label="Live source lag"
            value={
              materialization?.source_record_lag.available
                ? count(materialization.source_record_lag.value)
                : "—"
            }
            detail="No exact unmaterialized-observation count available"
          />
        </div>
      )}

      <div className="grid items-start gap-6 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Projection registry</CardTitle>
            <CardDescription>
              The deployed registry defines one complete generation from
              ingested observations.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {workloads.length ? (
              workloads.map((workload) => (
                <div key={workload.name} className="space-y-3">
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <code>{workload.source}</code>
                    <span aria-hidden="true">→</span>
                    <span>{workload.projections.length} projections</span>
                  </div>
                  <div className="divide-y rounded-lg border">
                    {workload.projections.map((projection) => (
                      <a
                        key={projection}
                        href={`/?sql=${encodeURIComponent(
                          `DESCRIBE ${projection
                            .split(".")
                            .map((part) => `"${part.replaceAll('"', '""')}"`)
                            .join(".")};`
                        )}`}
                        className="flex items-center justify-between gap-3 px-3 py-3 text-xs hover:bg-muted/50"
                      >
                        <code className="break-all">{projection}</code>
                        <ArrowUpRightIcon
                          className="size-3.5 shrink-0 text-muted-foreground"
                          aria-label="Inspect in console"
                        />
                      </a>
                    ))}
                  </div>
                </div>
              ))
            ) : (
              <p className="text-sm text-muted-foreground">
                Projection registry unavailable.
              </p>
            )}
            <p className="text-xs text-muted-foreground">
              This is the API’s deployed registry. It does not verify the active
              generation’s registry digest or prove that live processing has
              caught up.
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Generation and live catch-up</CardTitle>
            <CardDescription>
              Completed rebuild records and current CDC coverage are separate
              evidence.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {latestCompleted ? (
              <div className="rounded-lg border p-3">
                <p className="text-xs text-muted-foreground">
                  Latest completed rebuild in the returned history
                </p>
                <code className="mt-1 block text-xs break-all">
                  {latestCompleted.id}
                </code>
                <dl className="mt-3 grid grid-cols-2 gap-3 text-xs">
                  <Detail
                    label="Source snapshot"
                    value={count(latestCompleted.source_snapshot)}
                  />
                  <Detail
                    label="Covered snapshot"
                    value={count(latestCompleted.covered_snapshot)}
                  />
                  <Detail
                    label="Activation snapshot"
                    value={count(latestCompleted.activation_snapshot)}
                  />
                  <Detail
                    label="Completed"
                    value={date(latestCompleted.completed_at)}
                  />
                </dl>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                {runs.data
                  ? "No completed rebuild in the returned history."
                  : "Completed rebuild state unavailable."}
              </p>
            )}
            <div className="rounded-lg border border-dashed p-3 text-xs text-muted-foreground">
              <p className="font-medium text-foreground">
                Live coverage is not measured here
              </p>
              <p className="mt-1">
                {materialization?.source_record_lag.reason ??
                  "The current status API does not expose the active generation marker or durable CDC cursor."}
              </p>
              <p className="mt-2">
                An empty delivery queue does not prove that projections are
                current. A completed rebuild is historical evidence, not a check
                of the active generation today.
              </p>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Batch delivery</CardTitle>
          <CardDescription>
            Live and rebuild visit batches share this delivery lane. Counts
            represent batches, not observations.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Workload</TableHead>
                <TableHead className="text-right">Pending</TableHead>
                <TableHead className="text-right">Unacknowledged</TableHead>
                <TableHead className="text-right">Redelivered</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {workloads.map((workload) => (
                <TableRow key={workload.name}>
                  <TableCell>
                    <code>{workload.source}</code>
                    {!workload.queue.available && (
                      <Badge className="ml-2" variant="outline">
                        Unavailable
                      </Badge>
                    )}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {count(workload.queue.pending)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {count(workload.queue.ack_pending)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {count(workload.queue.redelivered)}
                  </TableCell>
                </TableRow>
              ))}
              {!workloads.length && (
                <TableRow>
                  <TableCell
                    colSpan={4}
                    className="h-20 text-center text-muted-foreground"
                  >
                    Delivery state unavailable.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
          <p className="mt-3 text-xs text-muted-foreground">
            Unacknowledged batches may be processing or awaiting redelivery.
            Redelivered counts overlap outstanding delivery state; they are not
            a lifetime failure count. Planner and activation queue counts are
            not exposed by this API.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Materializer capacity</CardTitle>
          <CardDescription>
            Existing process presence reports. Capacity does not indicate
            processing throughput.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Detail
              label="Configured slots"
              value={count(workers?.configured_capacity)}
            />
            <Detail
              label="Usable slots"
              value={count(workers?.usable_capacity)}
            />
            <Detail
              label="Active operations"
              value={count(workers?.active_operations)}
            />
            <Detail
              label="Degraded slots"
              value={count(workers?.degraded_capacity)}
            />
          </dl>
          {workers && workers.degraded_capacity > 0 && (
            <p className="mt-4 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-xs">
              {count(workers.degraded_capacity)} configured slots are not
              currently usable.
            </p>
          )}
          {workers && workers.usable_capacity === 0 && (
            <p className="mt-4 text-xs text-muted-foreground">
              No usable materializer capacity is reported. Presence does not
              identify whether a process is stopped, starting, or unhealthy.
            </p>
          )}
        </CardContent>
      </Card>

      {active.map((run) => (
        <ActiveRebuild key={run.id} run={run} />
      ))}
      <RebuildHistory runs={runs.data} loading={runs.isPending} />
      <p className="text-xs leading-relaxed text-muted-foreground">
        Read-only view of existing operational state, refreshed every 5 seconds.
        No samples or additional history are stored. Ingestion delivery is on{" "}
        <a className="underline underline-offset-4" href="/data/ingestion">
          Ingestion
        </a>
        ; physical bytes are on{" "}
        <a className="underline underline-offset-4" href="/data/storage">
          Storage
        </a>
        .
      </p>
    </div>
  )
}

function ActiveRebuild({ run }: { run: MaterializationRun }) {
  const progress = Math.min(100, Math.max(0, Math.round(run.progress * 100)))
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>{phaseLabels[run.status]}</CardTitle>
          <RunStatus run={run} />
        </div>
        <CardDescription>
          <code className="break-all">{run.id}</code>
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Progress value={progress}>
          <ProgressLabel>
            {count(run.completed_batches)} of {count(run.total_batches)} batches
            committed
          </ProgressLabel>
          <ProgressValue>{() => `${progress}%`}</ProgressValue>
        </Progress>
        <p className="text-xs text-muted-foreground">
          Batch progress is not an ETA. Catch-up may add batches before the
          complete generation activates atomically.
        </p>
        <dl className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <Detail label="Source observations" value={count(run.source_items)} />
          <Detail label="Output rows" value={count(run.output_rows)} />
          <Detail label="Output bytes" value={bytes(run.output_bytes)} />
          <Detail label="Started" value={date(run.started_at)} />
        </dl>
        {run.error && (
          <p className="text-xs break-words text-destructive">{run.error}</p>
        )}
      </CardContent>
    </Card>
  )
}

function RebuildHistory({
  runs,
  loading,
}: {
  runs: MaterializationRun[] | undefined
  loading: boolean
}) {
  const [filter, setFilter] = useState<"all" | "failed" | "completed">("all")
  const [page, setPage] = useState(0)
  const filtered =
    runs?.filter((run) => filter === "all" || run.status === filter) ?? []
  const safePage = Math.min(
    page,
    Math.max(0, Math.ceil(filtered.length / 10) - 1)
  )
  const visible = filtered.slice(safePage * 10, (safePage + 1) * 10)
  return (
    <Card>
      <CardHeader>
        <CardTitle>Rebuild history</CardTitle>
        <CardDescription>
          Up to the 100 most recent persisted runs. Expand a run for its source
          and activation snapshots.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex gap-2">
          {(["all", "failed", "completed"] as const).map((value) => (
            <Button
              key={value}
              variant={filter === value ? "secondary" : "ghost"}
              size="sm"
              aria-pressed={filter === value}
              onClick={() => {
                setFilter(value)
                setPage(0)
              }}
              className="capitalize"
            >
              {value}
            </Button>
          ))}
        </div>
        <div className="divide-y rounded-lg border">
          {visible.map((run) => (
            <details key={run.id} className="group">
              <summary className="cursor-pointer px-3 py-3 hover:bg-muted/30">
                <span className="ml-2 inline-flex max-w-[90%] flex-wrap items-center gap-x-4 gap-y-2 align-middle">
                  <code className="text-xs" title={run.id}>
                    {run.id.slice(0, 12)}
                  </code>
                  <RunStatus run={run} />
                  <span className="text-xs text-muted-foreground">
                    {date(run.created_at)}
                  </span>
                  <span className="text-xs tabular-nums">
                    {count(run.completed_batches)} / {count(run.total_batches)}{" "}
                    batches
                  </span>
                </span>
              </summary>
              <div className="space-y-4 border-t bg-muted/10 p-4">
                {run.error && (
                  <p className="rounded-lg border border-destructive/30 p-3 text-xs break-words text-destructive">
                    {run.error}
                  </p>
                )}
                <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                  <Detail
                    label="Source snapshot"
                    value={count(run.source_snapshot)}
                  />
                  <Detail
                    label="Covered snapshot"
                    value={count(run.covered_snapshot)}
                  />
                  <Detail
                    label="Activation snapshot"
                    value={count(run.activation_snapshot)}
                  />
                  <Detail label="Batch size" value={count(run.batch_size)} />
                  <Detail
                    label="Source observations"
                    value={count(run.source_items)}
                  />
                  <Detail
                    label="Source bytes"
                    value={bytes(run.source_bytes)}
                  />
                  <Detail label="Output rows" value={count(run.output_rows)} />
                  <Detail
                    label="Output bytes"
                    value={bytes(run.output_bytes)}
                  />
                  <Detail label="Started" value={date(run.started_at)} />
                  <Detail label="Completed" value={date(run.completed_at)} />
                </dl>
                <p className="text-xs text-muted-foreground">
                  Registry digest{" "}
                  <code className="block break-all text-foreground">
                    {run.registry_digest}
                  </code>
                </p>
                <p className="text-xs text-muted-foreground">
                  Run{" "}
                  <code className="break-all text-foreground">{run.id}</code>
                </p>
              </div>
            </details>
          ))}
          {!visible.length && (
            <p className="p-6 text-center text-sm text-muted-foreground">
              {loading
                ? "Reading rebuild history…"
                : !runs
                  ? "Rebuild history unavailable."
                  : filter === "all"
                    ? "No rebuilds recorded."
                    : `No ${filter} rebuilds in the returned history.`}
            </p>
          )}
        </div>
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs text-muted-foreground">
            {count(filtered.length)} runs
            {runs?.length === 100 ? " · limited to latest 100" : ""}
          </p>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={safePage === 0}
              onClick={() => setPage(safePage - 1)}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={(safePage + 1) * 10 >= filtered.length}
              onClick={() => setPage(safePage + 1)}
            >
              Next
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function RunStatus({ run }: { run: MaterializationRun }) {
  return (
    <Badge
      className="capitalize"
      variant={
        run.status === "failed"
          ? "destructive"
          : run.status === "completed"
            ? "secondary"
            : "outline"
      }
    >
      {run.status}
    </Badge>
  )
}
function Metric({
  label,
  value,
  detail,
}: {
  label: string
  value: string
  detail: string
}) {
  return (
    <Card>
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <p className="text-3xl font-semibold tracking-tight tabular-nums">
          {value}
        </p>
      </CardHeader>
      <CardContent className="text-xs text-muted-foreground">
        {detail}
      </CardContent>
    </Card>
  )
}
function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-1 text-sm tabular-nums">{value}</dd>
    </div>
  )
}
function ReadError({
  label,
  error,
  stale,
}: {
  label: string
  error: unknown
  stale: boolean
}) {
  return (
    <div
      role="alert"
      className="rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-sm"
    >
      {label}: {extractApiError(error)}
      {stale && " Showing the last successful response."}
    </div>
  )
}
