import {
  ChevronDownIcon,
  LoaderCircleIcon,
  TriangleAlertIcon,
  WrenchIcon,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
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
import { useDataStatus } from "@/hooks/use-data-status"
import { extractApiError } from "@/lib/api"
import type {
  DataStatus,
  DataStatusState,
  DeliveryQueue,
  MaterializationRunSummary,
  MaterializationWorkload,
  WorkerCapacity,
} from "@/types/operations"

export function DataMetricsPage() {
  const statusQuery = useDataStatus()

  if (statusQuery.isLoading) {
    return (
      <LoaderCircleIcon className="m-auto size-5 animate-spin text-muted-foreground" />
    )
  }

  if (statusQuery.error || !statusQuery.data) {
    return (
      <Card className="border-destructive/40">
        <CardHeader>
          <CardTitle>Data status is unavailable</CardTitle>
          <CardDescription>
            {statusQuery.error
              ? extractApiError(statusQuery.error)
              : "Atlas did not return a data status."}
          </CardDescription>
        </CardHeader>
      </Card>
    )
  }

  const status = statusQuery.data
  const visits = status.materialization.workloads.find(
    (workload) => workload.name === "visits"
  )
  const materializationRuns = status.materialization_runs.filter(
    (run) => run.status !== "completed"
  )

  return (
    <div className="flex w-full min-w-0 flex-col gap-4 pb-2">
      <DataVerdict status={status} />
      <section className="grid gap-4 lg:grid-cols-2">
        <PipelineCard
          title="Ingestion"
          description="Accepted evidence moving into DuckLake."
          status={status.ingestion.status}
          queue={status.ingestion.queue}
          countLabel="evidence jobs waiting"
          warning={
            status.ingestion.dead_letters > 0
              ? `${status.ingestion.dead_letters.toLocaleString()} dead-lettered`
              : undefined
          }
        />
        <MaterializationCard
          title="Materialization"
          description="Visit-scoped batches projecting the complete material schema."
          workload={visits}
          status={status.materialization.status}
        />
      </section>
      {materializationRuns.length > 0 ? (
        <RebuildStatus runs={materializationRuns} />
      ) : null}
      <DataDiagnostics status={status} />
    </div>
  )
}

function DataVerdict({ status }: { status: DataStatus }) {
  const copy = {
    current: {
      title: "Caught up",
      description: "Known ingestion and materialization queues are caught up.",
    },
    processing: {
      title: "Processing data",
      description: "Atlas is working through incoming evidence.",
    },
    attention: {
      title: "Needs attention",
      description: "Some data work is failing or repeatedly being delivered.",
    },
    unavailable: {
      title: "Status unavailable",
      description: "One or more data workloads cannot report their state.",
    },
  }[status.status]

  return (
    <Card>
      <CardHeader className="border-b">
        <CardTitle className="text-base">{copy.title}</CardTitle>
        <CardDescription>{copy.description}</CardDescription>
        <CardAction>
          <StatusBadge status={status.status} />
        </CardAction>
      </CardHeader>
      <CardContent>
        {status.source_record_lag.available ? (
          <div>
            <p className="text-3xl font-semibold tabular-nums">
              {(status.source_record_lag.value ?? 0).toLocaleString()}
            </p>
            <p className="mt-1 text-sm text-muted-foreground">
              source records not query-ready
            </p>
          </div>
        ) : (
          <p className="max-w-3xl text-xs/relaxed text-muted-foreground">
            Exact source-row lag is not available yet. The workload counts below
            are delivery jobs and CDC messages—not rows—and are shown only as
            current processing signals.
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function MaterializationCard({
  title,
  description,
  workload,
  status,
}: {
  title: string
  description: string
  workload?: MaterializationWorkload
  status: DataStatusState
}) {
  const inferredStatus =
    workload && !workload.queue.available
      ? "unavailable"
      : (workload?.queue.total ?? 0) > 0
        ? status === "attention"
          ? "attention"
          : "processing"
        : status === "unavailable"
          ? "unavailable"
          : "current"

  return (
    <PipelineCard
      title={title}
      description={description}
      status={inferredStatus}
      queue={workload?.queue}
      countLabel="CDC updates waiting"
      detail={
        workload ? (
          <>
            <code className="font-mono text-[0.6875rem] text-muted-foreground">
              {workload.source}
            </code>
            <p className="mt-2 text-xs text-muted-foreground">
              {workload.projections.map(projectionLabel).join(" · ")}
            </p>
          </>
        ) : (
          <p className="text-xs text-muted-foreground">
            Workload status unavailable
          </p>
        )
      }
    />
  )
}

function PipelineCard({
  title,
  description,
  status,
  queue,
  countLabel,
  detail,
  warning,
}: {
  title: string
  description: string
  status: DataStatusState
  queue?: DeliveryQueue
  countLabel: string
  detail?: React.ReactNode
  warning?: string
}) {
  const count = queue?.available ? queue.total : null

  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
        <CardAction>
          <StatusBadge status={status} />
        </CardAction>
      </CardHeader>
      <CardContent>
        <p className="text-3xl font-semibold tabular-nums">
          {count === null || count === undefined ? "—" : count.toLocaleString()}
        </p>
        <p className="mt-1 text-sm text-muted-foreground">{countLabel}</p>
        {warning ? (
          <p className="mt-3 inline-flex items-center gap-1.5 text-xs font-medium text-destructive">
            <TriangleAlertIcon className="size-3.5" />
            {warning}
          </p>
        ) : null}
        {detail ? <div className="mt-4 border-t pt-3">{detail}</div> : null}
      </CardContent>
    </Card>
  )
}

function RebuildStatus({ runs }: { runs: MaterializationRunSummary[] }) {
  return (
    <Card>
      <CardHeader className="border-b">
        <CardTitle className="flex items-center gap-2">
          <WrenchIcon className="size-4 text-muted-foreground" />
          Materialization rebuilds
        </CardTitle>
        <CardDescription>
          Complete hidden generations are built from bounded visit batches.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        {runs.map((run) => (
          <div
            key={run.id}
            className="grid gap-3 rounded-md border bg-muted/10 p-3 sm:grid-cols-[minmax(0,1fr)_auto]"
          >
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <Badge
                  variant={run.status === "failed" ? "destructive" : "outline"}
                  className="capitalize"
                >
                  {run.status}
                </Badge>
                <code className="font-mono text-[0.6875rem] text-muted-foreground">
                  {run.id.slice(0, 8)}
                </code>
              </div>
              <p className="mt-2 truncate text-xs text-muted-foreground">
                {run.completed_batches.toLocaleString()} of{" "}
                {run.total_batches.toLocaleString()} batches complete
              </p>
              {run.error ? (
                <p className="mt-2 text-xs text-destructive">{run.error}</p>
              ) : null}
            </div>
            <div className="text-left sm:text-right">
              <p className="font-medium tabular-nums">
                {run.source_items.toLocaleString()} inputs
              </p>
              <p className="mt-1 text-xs text-muted-foreground tabular-nums">
                {formatBytes(run.source_bytes)} ·{" "}
                {run.output_rows.toLocaleString()} rows
              </p>
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  )
}

function DataDiagnostics({ status }: { status: DataStatus }) {
  return (
    <Collapsible>
      <Card>
        <CardHeader>
          <CardTitle>Operations diagnostics</CardTitle>
          <CardDescription>
            Worker capacity and transport-level queue state.
          </CardDescription>
          <CardAction>
            <CollapsibleTrigger className="group inline-flex items-center gap-2 rounded-md px-2 py-1 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground">
              Details
              <ChevronDownIcon className="size-3.5 transition-transform group-data-[panel-open]:rotate-180" />
            </CollapsibleTrigger>
          </CardAction>
        </CardHeader>
        <CollapsibleContent>
          <CardContent className="grid gap-4 border-t pt-4">
            <WorkerDiagnostics
              title="Ingestion workers"
              workers={status.ingestion.workers}
              queues={[["Evidence jobs", status.ingestion.queue]]}
            />
            <WorkerDiagnostics
              title="Materialization workers"
              workers={status.materialization.workers}
              queues={status.materialization.workloads.map((workload) => [
                sentenceCase(workload.name),
                workload.queue,
              ])}
            />
          </CardContent>
        </CollapsibleContent>
      </Card>
    </Collapsible>
  )
}

function WorkerDiagnostics({
  title,
  workers,
  queues,
}: {
  title: string
  workers: WorkerCapacity
  queues: Array<[string, DeliveryQueue]>
}) {
  return (
    <div className="rounded-md border">
      <div className="border-b bg-muted/20 px-3 py-2">
        <p className="font-medium">{title}</p>
      </div>
      <div className="grid gap-3 p-3 sm:grid-cols-2 lg:grid-cols-5">
        <DiagnosticValue
          value={workers.worker_count.toLocaleString()}
          label="replicas"
        />
        <DiagnosticValue
          value={`${workers.active_operations.toLocaleString()} / ${workers.usable_capacity.toLocaleString()}`}
          label="active / usable slots"
        />
        <DiagnosticValue
          value={workers.configured_capacity.toLocaleString()}
          label="configured slots"
        />
        <DiagnosticValue
          value={workers.degraded_capacity.toLocaleString()}
          label="degraded slots"
          attention={workers.degraded_capacity > 0}
        />
      </div>
      <div className="grid gap-3 border-t p-3 lg:grid-cols-2">
        {queues.map(([label, queue]) => (
          <QueueDiagnostics key={label} label={label} queue={queue} />
        ))}
      </div>
    </div>
  )
}

function QueueDiagnostics({
  label,
  queue,
}: {
  label: string
  queue: DeliveryQueue
}) {
  if (!queue.available) {
    return (
      <div className="rounded-md bg-muted/20 p-3">
        <p className="font-medium">{label}</p>
        <p className="mt-1 text-xs text-muted-foreground">
          Queue state unavailable
        </p>
      </div>
    )
  }

  return (
    <div className="rounded-md bg-muted/20 p-3">
      <p className="font-medium">{label}</p>
      <p className="mt-1 text-xs text-muted-foreground tabular-nums">
        {formatCount(queue.pending)} pending · {formatCount(queue.ack_pending)}{" "}
        ACK-pending · {formatCount(queue.redelivered)} redelivered ·{" "}
        {formatCount(queue.waiting_for_redelivery)} awaiting redelivery
      </p>
    </div>
  )
}

function DiagnosticValue({
  value,
  label,
  attention = false,
}: {
  value: string
  label: string
  attention?: boolean
}) {
  return (
    <div className="rounded-md bg-muted/20 p-3">
      <p
        className={`font-medium tabular-nums ${attention ? "text-destructive" : ""}`}
      >
        {value}
      </p>
      <p className="mt-1 text-xs text-muted-foreground">{label}</p>
    </div>
  )
}

function StatusBadge({ status }: { status: DataStatusState }) {
  const label = {
    current: "Current",
    processing: "Processing",
    attention: "Needs attention",
    unavailable: "Unavailable",
  }[status]

  return (
    <Badge
      variant={
        status === "attention" || status === "unavailable"
          ? "destructive"
          : "outline"
      }
      className={
        status === "processing"
          ? "border-primary/30 bg-primary/10 text-primary"
          : undefined
      }
    >
      {label}
    </Badge>
  )
}

function projectionLabel(value: string) {
  return value
    .replace(/^material\./, "")
    .split("_")
    .map((part) => sentenceCase(part))
    .join(" ")
}

function sentenceCase(value: string) {
  return value.charAt(0).toUpperCase() + value.slice(1)
}

function formatCount(value: number | null) {
  return value === null ? "—" : value.toLocaleString()
}

function formatBytes(bytes: number) {
  if (bytes < 1_024) return `${bytes.toLocaleString()} B`
  if (bytes < 1_048_576) return `${(bytes / 1_024).toFixed(1)} KB`
  if (bytes < 1_073_741_824) return `${(bytes / 1_048_576).toFixed(1)} MB`
  return `${(bytes / 1_073_741_824).toFixed(1)} GB`
}
