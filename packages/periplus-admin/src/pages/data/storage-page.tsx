import { useMemo, useState } from "react"
import {
  ArrowDownIcon,
  ArrowUpIcon,
  ArrowUpRightIcon,
  Clock3Icon,
  DatabaseIcon,
  RefreshCwIcon,
} from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useStorage } from "@/hooks/use-storage"
import { extractApiError } from "@/lib/api"
import type {
  LakeStorageTable,
  StorageReport,
  StorageSource,
} from "@/types/storage"
import { accountedBytes, bytes, perObservation } from "./storage-format"

const colors: Record<string, string> = {
  raw: "bg-amber-400",
  lake: "bg-sky-400",
  control: "bg-violet-400",
  metadata: "bg-teal-400",
  transient: "bg-rose-400",
  nats: "bg-slate-400",
}
const count = (value: number | null | undefined) =>
  value == null ? "—" : value.toLocaleString()
const date = (value: string | null | undefined) =>
  value ? new Date(value).toLocaleString() : "Not recorded"

export function StoragePage() {
  const query = useStorage()
  const data = query.data
  const measured = data ? accountedBytes(data.sources) : null
  const pending = data?.retention?.stages.reduce(
    (sum, stage) => sum + stage.expected_bytes,
    0
  )

  return (
    <div className="mx-auto flex w-full max-w-7xl flex-col gap-6 pb-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Storage</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Footprint, retained evidence, and the path to reclamation.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-muted-foreground">
            {data
              ? `Measured ${date(data.collected_at)}`
              : "Reading storage metadata"}
          </span>
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
      </div>
      {query.error && (
        <div
          role="alert"
          className="rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-sm"
        >
          {data && "Showing the last successful measurement. "}
          {extractApiError(query.error)}
        </div>
      )}
      {!data && query.isPending ? (
        <div aria-label="Loading storage" className="grid gap-4 md:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-32" />
          ))}
        </div>
      ) : data ? (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Metric
              label="Accounted footprint"
              value={bytes(measured)}
              detail="Partial coverage · current files and measured stores"
              badge="Partial"
            />
            <Metric
              label="Retained observations"
              value={count(data.evidence?.observations)}
              detail="Current evidence in ingest.visits"
            />
            <Metric
              label="Pending raw reclamation"
              value={bytes(pending)}
              detail={
                data.retention
                  ? `${count(data.retention.stages.reduce((sum, stage) => sum + stage.objects, 0))} queued objects · expected bytes`
                  : "Retention state unavailable"
              }
            />
            <Metric
              label="Net storage growth"
              value="—"
              detail="Historical size measurements are not recorded"
            />
          </div>
          {data.issues.length > 0 && (
            <div
              role="status"
              className="rounded-lg border border-amber-500/30 bg-amber-500/5 px-4 py-3 text-sm"
            >
              {data.issues.join(" ")}
            </div>
          )}
          <div className="grid gap-6 xl:grid-cols-[2fr_1fr]">
            <FootprintCard sources={data.sources} total={measured} />
            <Card>
              <CardHeader>
                <CardTitle>Growth over time</CardTitle>
                <CardDescription>
                  Storage added, reclaimed, and net change.
                </CardDescription>
              </CardHeader>
              <CardContent className="flex flex-1 flex-col justify-center gap-4 py-5">
                <Clock3Icon className="size-6 text-muted-foreground/60" />
                <div>
                  <p className="text-sm font-medium">
                    No historical measurements
                  </p>
                  <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
                    Current sizes cannot establish a growth rate. This page
                    stores no samples, so growth, reclaimed-byte trends, and
                    capacity runway are unavailable.
                  </p>
                </div>
                <span className="text-xs text-muted-foreground">
                  Observation timestamps are not a storage history.
                </span>
              </CardContent>
            </Card>
          </div>
          <EvidenceCard data={data} />
          <LakeTables
            tables={data.tables}
            complete={data.tables_complete}
            available={
              data.sources.find((source) => source.id === "lake")?.bytes != null
            }
          />
          <RetentionCard data={data} />
          <OperationsCard data={data} />
          <p className="text-xs leading-relaxed text-muted-foreground">
            Read-only measurements · Cached for up to 60 seconds · Sources are
            collected independently between {date(data.as_of)} and{" "}
            {date(data.collected_at)}. Totals exclude historical and
            unreferenced lake files, backups, WAL, NATS KV and replication
            overhead. Overall capacity limits are not measured here.
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
  badge,
}: {
  label: string
  value: string
  detail: string
  badge?: string
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between text-xs text-muted-foreground">
          {label}
          {badge && <Badge variant="outline">{badge}</Badge>}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-3xl font-medium tracking-tight tabular-nums">
          {value}
        </p>
        <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
          {detail}
        </p>
      </CardContent>
    </Card>
  )
}

function FootprintCard({
  sources,
  total,
}: {
  sources: StorageSource[]
  total: number | null
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Where the bytes live</CardTitle>
        <CardDescription>
          Measured components of the footprint. Some physical storage is outside
          this view.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div
          className="flex h-4 overflow-hidden rounded-sm bg-muted"
          role="img"
          aria-label="Storage distribution across measured sources"
        >
          {total != null &&
            total > 0 &&
            sources.map((source) =>
              source.bytes != null && source.bytes > 0 ? (
                <div
                  key={source.id}
                  className={colors[source.id]}
                  style={{ width: `${(source.bytes / total) * 100}%` }}
                  title={`${source.name}: ${bytes(source.bytes)}`}
                />
              ) : null
            )}
        </div>
        <div className="grid gap-x-6 gap-y-4 sm:grid-cols-2">
          {sources.map((source) => (
            <div key={source.id} className="min-w-0">
              <div className="flex items-center gap-2 text-xs">
                <span
                  className={`size-2 shrink-0 rounded-full ${colors[source.id]}`}
                />
                <span>{source.name}</span>
                <span className="ml-auto font-medium tabular-nums">
                  {source.bytes != null && !source.complete ? "≥ " : ""}
                  {bytes(source.bytes)}
                </span>
              </div>
              <p className="mt-1 pl-4 text-[11px] leading-relaxed text-muted-foreground">
                {source.reason ?? source.basis}
              </p>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}

function EvidenceCard({ data }: { data: StorageReport }) {
  const evidence = data.evidence
  return (
    <Card>
      <CardHeader>
        <CardTitle>Evidence footprint</CardTitle>
        <CardDescription>
          Stored document sizes from the catalogue. Shared object keys are
          counted once in unique bytes.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {evidence ? (
          <>
            <div className="grid grid-cols-2 gap-5 md:grid-cols-4">
              <Value
                label="Document records"
                value={count(evidence.documents)}
              />
              <Value
                label="Unique referenced objects"
                value={count(evidence.unique_objects)}
              />
              <Value
                label="Unique referenced bytes"
                value={bytes(evidence.unique_bytes)}
              />
              <Value
                label="Observations with documents"
                value={`${count(evidence.observations_with_documents)} / ${count(evidence.observations)}`}
              />
            </div>
            <div className="mt-5 grid grid-cols-2 gap-5 border-t pt-5 md:grid-cols-4">
              <Value
                label="Median document size"
                value={bytes(evidence.median_bytes)}
              />
              <Value
                label="p95 document size"
                value={bytes(evidence.p95_bytes)}
              />
              <Value
                label="Referenced bytes / observation"
                value={bytes(
                  perObservation(
                    evidence.referenced_bytes,
                    evidence.observations
                  )
                )}
              />
              <Value
                label="Amortized bytes / observation"
                value={bytes(
                  perObservation(evidence.unique_bytes, evidence.observations)
                )}
              />
            </div>
            <p className="mt-4 text-xs text-muted-foreground">
              Referenced bytes count each document reference; amortized bytes
              divide unique retained object bytes across all observations.
              Neither includes lake projections or operational overhead. Raw
              inventory also includes objects no longer referenced by retained
              documents.
            </p>
          </>
        ) : (
          <Unavailable text="Document and observation measurements could not be read." />
        )}
      </CardContent>
    </Card>
  )
}

function Value({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-lg font-medium tabular-nums">{value}</p>
    </div>
  )
}

function LakeTables({
  tables,
  complete,
  available,
}: {
  tables: LakeStorageTable[]
  complete: boolean
  available: boolean
}) {
  const [filter, setFilter] = useState("")
  const [role, setRole] = useState("all")
  const [sort, setSort] = useState<"bytes" | "estimated_rows" | "files">(
    "bytes"
  )
  const [descending, setDescending] = useState(true)
  const [page, setPage] = useState(0)
  const rows = useMemo(
    () =>
      tables
        .filter(
          (table) =>
            (role === "all" || table.role === role) &&
            `${table.schema_name}.${table.name}`
              .toLowerCase()
              .includes(filter.toLowerCase())
        )
        .sort((a, b) => (descending ? -1 : 1) * (a[sort] - b[sort])),
    [tables, role, filter, sort, descending]
  )
  const pageCount = Math.max(1, Math.ceil(rows.length / 20))
  const currentPage = Math.min(page, pageCount - 1)
  function order(key: typeof sort) {
    setDescending(key === sort ? !descending : true)
    setSort(key)
  }
  function heading(label: string, key: typeof sort) {
    return (
      <Button
        variant="ghost"
        size="sm"
        className="-mr-2 h-7 px-2 text-xs"
        onClick={() => order(key)}
      >
        {label}
        {sort === key && (descending ? <ArrowDownIcon /> : <ArrowUpIcon />)}
      </Button>
    )
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>DuckLake tables</CardTitle>
        <CardDescription>
          Current registered data and delete files. Views store no additional
          copy. Row counts are metadata estimates.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {!available ? (
          <Unavailable text="Table measurements are unavailable." />
        ) : (
          <>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap gap-1">
                {["all", "evidence", "projection", "bookkeeping", "other"].map(
                  (value) => (
                    <Button
                      key={value}
                      size="sm"
                      variant={role === value ? "secondary" : "ghost"}
                      aria-pressed={role === value}
                      className="capitalize"
                      onClick={() => {
                        setRole(value)
                        setPage(0)
                      }}
                    >
                      {value}
                    </Button>
                  )
                )}
              </div>
              <Input
                aria-label="Filter DuckLake tables"
                placeholder="Find a table…"
                className="w-full sm:w-60"
                value={filter}
                onChange={(event) => {
                  setFilter(event.target.value)
                  setPage(0)
                }}
              />
            </div>
            {!complete && (
              <p className="text-xs text-amber-600">
                Partial table inventory: the first 200 tables were measured.
              </p>
            )}
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Relation</TableHead>
                  <TableHead>Role / generation</TableHead>
                  <TableHead className="text-right">
                    {heading("Rows (est.)", "estimated_rows")}
                  </TableHead>
                  <TableHead className="text-right">
                    {heading("File bytes", "bytes")}
                  </TableHead>
                  <TableHead className="text-right">
                    {heading("Data files", "files")}
                  </TableHead>
                  <TableHead className="text-right">Avg. data file</TableHead>
                  <TableHead>
                    <span className="sr-only">Inspect</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows
                  .slice(currentPage * 20, (currentPage + 1) * 20)
                  .map((table) => (
                    <TableRow key={`${table.schema_name}.${table.name}`}>
                      <TableCell className="max-w-80">
                        <p
                          className="truncate font-mono text-xs"
                          title={`${table.schema_name}.${table.name}`}
                        >
                          {table.schema_name}.{table.name}
                        </p>
                        {table.delete_files > 0 && (
                          <p className="mt-1 text-[11px] text-muted-foreground">
                            {bytes(table.delete_bytes)} in{" "}
                            {count(table.delete_files)} delete files
                          </p>
                        )}
                      </TableCell>
                      <TableCell>
                        <span className="text-xs capitalize">{table.role}</span>
                        {table.generation !== "current" && (
                          <Badge variant="outline" className="ml-2 capitalize">
                            {table.generation}
                          </Badge>
                        )}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {count(table.estimated_rows)}
                      </TableCell>
                      <TableCell className="text-right font-medium tabular-nums">
                        {bytes(table.bytes)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {count(table.files)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {bytes(
                          table.files ? table.data_bytes / table.files : null
                        )}
                      </TableCell>
                      <TableCell>
                        <a
                          className="inline-flex p-1 text-muted-foreground hover:text-foreground"
                          aria-label={`Inspect ${table.schema_name}.${table.name} in console`}
                          href={`/?${new URLSearchParams({ sql: `DESCRIBE "${table.schema_name.replaceAll('"', '""')}"."${table.name.replaceAll('"', '""')}";` })}`}
                        >
                          <ArrowUpRightIcon className="size-4" />
                        </a>
                      </TableCell>
                    </TableRow>
                  ))}
                {rows.length === 0 && (
                  <TableRow>
                    <TableCell
                      colSpan={7}
                      className="py-8 text-center text-muted-foreground"
                    >
                      {tables.length
                        ? "No tables match these filters."
                        : "No tables in this measurement."}
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
            <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
              <span>
                {count(rows.length)} tables ·{" "}
                {bytes(rows.reduce((sum, row) => sum + row.bytes, 0))} current
                file bytes
              </span>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={currentPage === 0}
                  onClick={() => setPage(currentPage - 1)}
                >
                  Previous
                </Button>
                <span>
                  {currentPage + 1} / {pageCount}
                </span>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={currentPage + 1 === pageCount}
                  onClick={() => setPage(currentPage + 1)}
                >
                  Next
                </Button>
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              Zero file bytes can mean data is inlined in the metadata database.
              Old snapshots and unreferenced files can make physical lake usage
              larger than these totals.
            </p>
          </>
        )}
      </CardContent>
    </Card>
  )
}

function RetentionCard({ data }: { data: StorageReport }) {
  const retention = data.retention
  return (
    <Card>
      <CardHeader>
        <CardTitle>Retention & reclamation</CardTitle>
        <CardDescription>
          Logical retirement removes retained results. Physical reclamation
          follows snapshot expiry and reader grace.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {retention ? (
          <>
            <div className="grid gap-3 md:grid-cols-3">
              {retention.stages.map((stage, index) => (
                <div
                  key={stage.id}
                  className="rounded-lg border bg-muted/15 p-4"
                >
                  <p className="text-xs text-muted-foreground">
                    <span className="mr-2 font-mono">0{index + 1}</span>
                    {stage.name}
                  </p>
                  <p className="mt-3 text-2xl font-medium tabular-nums">
                    {count(stage.objects)}{" "}
                    <span className="text-xs font-normal text-muted-foreground">
                      objects
                    </span>
                  </p>
                  <p className="mt-1 text-xs">
                    {bytes(stage.expected_bytes)} expected bytes
                  </p>
                  <p className="mt-3 text-[11px] text-muted-foreground">
                    Oldest retirement:{" "}
                    {stage.objects ? date(stage.oldest_at) : "None queued"}
                  </p>
                </div>
              ))}
            </div>
            <div className="grid grid-cols-2 gap-5 border-t pt-5 lg:grid-cols-4">
              <Value
                label="Retired observation receipts"
                value={count(retention.retired_observations)}
              />
              <Value
                label="Retired request receipts"
                value={count(retention.retired_requests)}
              />
              <Value
                label="Retained lake snapshots"
                value={count(retention.snapshots)}
              />
              <Value
                label="Latest logical retirement"
                value={
                  retention.latest_retirement_at
                    ? new Date(
                        retention.latest_retirement_at
                      ).toLocaleDateString()
                    : "None recorded"
                }
              />
            </div>
            <p className="text-xs text-muted-foreground">
              Oldest retained snapshot: {date(retention.oldest_snapshot_at)}.
              Receipts protect against replay; they are not a cleanup rate.
              Queue sizes are expected object sizes, not verified reclaimable
              capacity. Reader-grace entries may still be blocked by publication
              claims or new references.
            </p>
          </>
        ) : (
          <Unavailable text="Retirement queue and receipt measurements are unavailable." />
        )}
        <div className="grid gap-4 border-t pt-5 md:grid-cols-2">
          <div>
            <p className="text-sm font-medium">Periplus janitor</p>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
              Owns result retirement, raw-object deletion, and transient
              cleanup. Running mode, last successful sweep, failures, and
              reclaimed-byte totals are not exposed by the current dashboard
              data sources.
            </p>
          </div>
          <div>
            <p className="text-sm font-medium">LakeDucktor</p>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
              Owns snapshot expiry, compaction, and obsolete lake-file removal.
              Maintenance history and reclaimed bytes are unavailable here.
              Retiring rows does not prove that files were removed.
            </p>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function OperationsCard({ data }: { data: StorageReport }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Operational storage</CardTitle>
        <CardDescription>
          Control state, lake metadata, delivery, and transient objects. These
          are included in the accounted footprint above.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
          {["control", "metadata", "nats", "transient"].map((id) => {
            const source = data.sources.find((item) => item.id === id)
            return (
              <div key={id}>
                <Value
                  label={source?.name ?? id}
                  value={`${source?.bytes != null && !source.complete ? "≥ " : ""}${bytes(source?.bytes)}`}
                />
                <p className="mt-2 text-xs text-muted-foreground">
                  {source?.reason ?? source?.basis}
                </p>
              </div>
            )
          })}
        </div>
        <details className="rounded-lg border">
          <summary className="cursor-pointer px-4 py-3 text-sm font-medium">
            Control tables & indexes{" "}
            <span className="ml-2 text-xs font-normal text-muted-foreground">
              {data.control_tables.length} relations
            </span>
          </summary>
          <div className="px-4 pb-4">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Relation</TableHead>
                  <TableHead className="text-right">Rows (est.)</TableHead>
                  <TableHead className="text-right">Table + TOAST</TableHead>
                  <TableHead className="text-right">Indexes</TableHead>
                  <TableHead className="text-right">Total</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.control_tables.map((table) => (
                  <TableRow key={table.name}>
                    <TableCell className="font-mono text-xs">
                      {table.name}
                    </TableCell>
                    <TableCell className="text-right">
                      {count(table.estimated_rows)}
                    </TableCell>
                    <TableCell className="text-right">
                      {bytes(table.table_bytes)}
                    </TableCell>
                    <TableCell className="text-right">
                      {bytes(table.index_bytes)}
                    </TableCell>
                    <TableCell className="text-right">
                      {bytes(table.total_bytes)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <p className="mt-3 text-xs text-muted-foreground">
              Largest 200 user tables. These are a breakdown of Control
              Postgres, not additional storage.
            </p>
          </div>
        </details>
        <details className="rounded-lg border">
          <summary className="cursor-pointer px-4 py-3 text-sm font-medium">
            Delivery streams
          </summary>
          <div className="px-4 pb-4">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Stream</TableHead>
                  <TableHead className="text-right">Messages</TableHead>
                  <TableHead className="text-right">Reported bytes</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.streams.map((stream) => (
                  <TableRow key={stream.name}>
                    <TableCell className="font-mono text-xs">
                      {stream.name}
                    </TableCell>
                    <TableCell className="text-right">
                      {count(stream.messages)}
                    </TableCell>
                    <TableCell className="text-right">
                      {bytes(stream.bytes)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </details>
      </CardContent>
    </Card>
  )
}

function Unavailable({ text }: { text: string }) {
  return (
    <div className="flex items-center gap-3 rounded-lg border border-dashed p-5 text-sm text-muted-foreground">
      <DatabaseIcon className="size-4 shrink-0" />
      {text}
    </div>
  )
}
