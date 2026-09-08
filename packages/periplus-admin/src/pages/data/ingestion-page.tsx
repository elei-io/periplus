import { ArrowDownToLineIcon, RefreshCwIcon } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useIngestion } from "@/hooks/use-ingestion"
import { extractApiError } from "@/lib/api"

const count = (value: number | null | undefined) =>
  value == null ? "—" : value.toLocaleString()
const time = (value: string) => new Date(value).toLocaleString()

export function IngestionPage() {
  const query = useIngestion()
  const report = query.data
  const queue = report?.queue
  const capacity = report?.capacity
  return (
    <div className="flex w-full min-w-0 flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Ingestion</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Captured evidence arriving in DuckLake.
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          disabled={query.isFetching}
          onClick={() => void query.refetch()}
        >
          <RefreshCwIcon className={query.isFetching ? "animate-spin" : ""} />
          Refresh
        </Button>
      </div>
      {query.error && (
        <Card className="border-destructive/40">
          <CardHeader>
            <CardTitle>
              {report
                ? "Refresh failed · showing previous state"
                : "Ingestion status unavailable"}
            </CardTitle>
            <CardDescription>{extractApiError(query.error)}</CardDescription>
          </CardHeader>
        </Card>
      )}
      {!report && query.isPending ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-32" />
          ))}
        </div>
      ) : report ? (
        <>
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <Badge
              variant={
                report.status === "attention" || report.status === "unavailable"
                  ? "destructive"
                  : "outline"
              }
            >
              {
                {
                  current: "Queue clear",
                  processing: "Processing",
                  attention: "Needs attention",
                  unavailable: "Unavailable",
                }[report.status]
              }
            </Badge>
            <span>Observed {time(report.generated_at)}</span>
          </div>
          {report.issues.length > 0 && (
            <Card className="border-amber-500/40">
              <CardContent className="pt-5 text-sm">
                {report.issues.map((issue) => (
                  <p key={issue}>{issue}</p>
                ))}
              </CardContent>
            </Card>
          )}
          <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Metric
              label="Outstanding jobs"
              value={count(queue?.total)}
              detail="Pending delivery + awaiting acknowledgement"
            />
            <Metric
              label="Active writer lanes"
              value={`${count(capacity?.active_operations)} / ${count(capacity?.usable_capacity)}`}
              detail="Active operations / usable capacity"
            />
            <Metric
              label="Ingestor replicas"
              value={count(capacity?.worker_count)}
              detail="Workers in current presence records"
            />
            <Metric
              label="Dead letters"
              value={count(report.dead_letters)}
              detail="Failed jobs retained for investigation"
              attention={(report.dead_letters ?? 0) > 0}
            />
          </section>
          <section className="grid gap-4 xl:grid-cols-[1.3fr_1fr]">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <ArrowDownToLineIcon className="size-4" />
                  Evidence delivery
                </CardTitle>
                <CardDescription>
                  One shared ingestion lane for observations and lineage.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-5">
                <div className="grid gap-4 sm:grid-cols-3">
                  <Value
                    label="Pending delivery"
                    value={count(queue?.pending)}
                  />
                  <Value
                    label="Awaiting ACK"
                    value={count(queue?.ack_pending)}
                  />
                  <Value
                    label="Redelivered"
                    value={count(queue?.redelivered)}
                  />
                </div>
                {!queue?.available && (
                  <p className="text-sm text-destructive">
                    The ingestion consumer could not be read.
                  </p>
                )}
                <p className="border-t pt-4 text-xs leading-relaxed text-muted-foreground">
                  Counts are delivery jobs, not observations or bytes. Awaiting
                  ACK includes work being processed and deliveries waiting to
                  retry. Redelivered messages are a subset; do not add them to
                  outstanding jobs.
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>What needs attention</CardTitle>
                <CardDescription>
                  Current delivery and writer signals.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3 text-sm">
                {report.dead_letters === null ? (
                  <p>Dead-letter count is unavailable.</p>
                ) : report.dead_letters > 0 ? (
                  <p className="text-destructive">
                    {count(report.dead_letters)} failed jobs remain in the
                    dead-letter stream.
                  </p>
                ) : (
                  <p>No retained ingestion dead letters.</p>
                )}
                {capacity === null ? (
                  <p>Worker capacity is unavailable.</p>
                ) : capacity?.usable_capacity === 0 ? (
                  <p className="text-destructive">
                    No usable writer lanes are reporting.
                  </p>
                ) : (
                  <p>
                    {count(capacity?.usable_capacity)} usable writer lanes;{" "}
                    {count(capacity?.degraded_capacity)} unavailable or
                    starting.
                  </p>
                )}
                {(queue?.redelivered ?? 0) > 0 && (
                  <p>
                    {count(queue?.redelivered)} messages have been redelivered.
                    Redelivery alone does not prove a failed commit.
                  </p>
                )}
                <p className="border-t pt-3 text-xs leading-relaxed text-muted-foreground">
                  Queue age, ingestion throughput and commit latency are not
                  available from these records. An empty queue does not prove
                  materialization readiness.
                </p>
              </CardContent>
            </Card>
          </section>
          <Card>
            <CardHeader>
              <CardTitle>Ingestor workers</CardTitle>
              <CardDescription>
                Existing worker presence and per-lane health. Replicas share the
                same durable consumer.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {report.workers === null ? (
                <p className="text-sm text-muted-foreground">
                  Worker presence is unavailable.
                </p>
              ) : report.workers.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No ingestors are currently reporting presence.
                </p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Worker</TableHead>
                      <TableHead>Process</TableHead>
                      <TableHead>Writer lanes</TableHead>
                      <TableHead>Last reported</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {report.workers.map((worker) => (
                      <TableRow key={worker.worker_id}>
                        <TableCell>
                          <p className="font-mono text-xs">
                            {worker.worker_id}
                          </p>
                          <p className="mt-1 text-xs text-muted-foreground">
                            Started {time(worker.started_at)}
                          </p>
                        </TableCell>
                        <TableCell>
                          <Badge
                            variant={
                              worker.process_ready ? "outline" : "destructive"
                            }
                          >
                            {worker.process_ready ? "Ready" : "Not ready"}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          <div className="flex flex-wrap gap-1">
                            {worker.lanes.map((lane) => (
                              <Badge
                                key={lane.lane_index}
                                variant={
                                  lane.status === "unavailable"
                                    ? "destructive"
                                    : "secondary"
                                }
                              >
                                Lane {lane.lane_index + 1}: {lane.status}
                                {lane.active && lane.status !== "active"
                                  ? " · active"
                                  : ""}
                              </Badge>
                            ))}
                          </div>
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {time(worker.last_seen_at)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Recent dead letters</CardTitle>
              <CardDescription>
                Newest retained failures. Inspection does not retry or change a
                job.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {report.recent_dead_letters === null ? (
                <p className="text-sm text-muted-foreground">
                  Failure details are unavailable.
                </p>
              ) : (
                <>
                  {report.recent_dead_letters.items.length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                      No ingestion failures in the inspected records.
                    </p>
                  ) : (
                    <div className="space-y-3">
                      {report.recent_dead_letters.items.map((item) => (
                        <div
                          key={item.sequence}
                          className="rounded-md border p-3"
                        >
                          <div className="flex flex-wrap items-center gap-2">
                            <Badge variant="outline">{item.kind}</Badge>
                            <span className="font-mono text-xs break-all">
                              {item.request_id}
                            </span>
                            <span className="text-xs text-muted-foreground">
                              Sequence {item.sequence}
                            </span>
                          </div>
                          <p className="mt-2 text-sm break-words">
                            {item.error}
                          </p>
                          <p className="mt-2 text-xs text-muted-foreground">
                            Failed {time(item.failed_at)} ·{" "}
                            {count(item.processing_failure_count)} processing
                            failures · Enqueued {time(item.enqueued_at)}
                          </p>
                        </div>
                      ))}
                    </div>
                  )}
                  {!report.recent_dead_letters.complete && (
                    <p className="mt-3 text-xs text-muted-foreground">
                      Bounded preview: up to 20 failures across 200 recent
                      stream sequences. Older failures may not be shown.
                    </p>
                  )}
                </>
              )}
            </CardContent>
          </Card>
          <p className="text-xs leading-relaxed text-muted-foreground">
            Refreshes every 10 seconds. Reads existing delivery and worker
            state; no measurement history is stored. Derived-table work is on{" "}
            <a
              className="underline underline-offset-4"
              href="/data/materialization"
            >
              Materialization
            </a>
            .
          </p>
        </>
      ) : null}
    </div>
  )
}

function Metric({
  label,
  value,
  detail,
  attention = false,
}: {
  label: string
  value: string
  detail: string
  attention?: boolean
}) {
  return (
    <Card>
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardTitle
          className={`text-3xl tabular-nums ${attention ? "text-destructive" : ""}`}
        >
          {value}
        </CardTitle>
      </CardHeader>
      <CardContent className="text-xs text-muted-foreground">
        {detail}
      </CardContent>
    </Card>
  )
}
function Value({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-2xl font-medium tabular-nums">{value}</p>
      <p className="mt-1 text-xs text-muted-foreground">{label}</p>
    </div>
  )
}
