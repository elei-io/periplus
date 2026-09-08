import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet"
import { apiUrl, apiErrorFromResponse, extractApiError } from "@/lib/api"
import type {
  QueryDashboard,
  QueryExecution,
  QueryExecutionPage,
  QueryStats,
  QueryBucket,
  QueryCount,
} from "@/types/query-history"

const ms = (n: number | null) =>
  n === null
    ? "—"
    : n < 1000
      ? `${n.toFixed(0)} ms`
      : `${(n / 1000).toFixed(2)} s`
const number = (n: number) => n.toLocaleString()
const percent = (n: number, total: number) =>
  total ? `${((100 * n) / total).toFixed(1)}%` : "—"
const stamp = (s: string) => new Date(s).toLocaleString()
async function read<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(apiUrl(path), { signal, cache: "no-store" })
  if (!response.ok) throw await apiErrorFromResponse(response)
  return response.json() as Promise<T>
}
function Filter({
  label,
  value,
  values,
  change,
}: {
  label: string
  value: string
  values: [string, string][]
  change: (value: string) => void
}) {
  return (
    <div className="space-y-1">
      <span className="text-xs text-muted-foreground">{label}</span>
      <Select
        value={value}
        onValueChange={(v) => {
          if (v) change(v)
        }}
      >
        <SelectTrigger aria-label={label} className="min-w-36">
          <SelectValue>
            {values.find(([key]) => key === value)?.[1]}
          </SelectValue>
        </SelectTrigger>
        <SelectContent>
          {values.map(([key, title]) => (
            <SelectItem key={key} value={key}>
              {title}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}
function Summary({ stats }: { stats: QueryStats }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {[
        [
          "Recorded executions",
          number(stats.executions),
          `${number(stats.successes)} successful`,
        ],
        ["p50 duration", ms(stats.p50_ms), "Successful operations only"],
        [
          "p95 duration",
          ms(stats.p95_ms),
          `${number(stats.successes)} samples${stats.successes < 20 ? " · small sample" : ""}`,
        ],
        [
          "Failed or rejected",
          percent(stats.failures, stats.executions),
          `${number(stats.failures)} operations · ${number(stats.truncated)} truncated results`,
        ],
      ].map(([label, value, note]) => (
        <Card key={label}>
          <CardHeader>
            <CardTitle className="text-sm text-muted-foreground">
              {label}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-3xl font-semibold tabular-nums">{value}</p>
            <p className="mt-2 text-xs text-muted-foreground">{note}</p>
          </CardContent>
        </Card>
      ))}
    </div>
  )
}
function Trend({ buckets, days }: { buckets: QueryBucket[]; days: number }) {
  // Include quiet UTC dates so the chart never joins across an invisible gap.
  const now = new Date()
  const data = Array.from({ length: days + 1 }, (_, i) => {
    const date = new Date(
      Date.UTC(
        now.getUTCFullYear(),
        now.getUTCMonth(),
        now.getUTCDate() - days + i
      )
    )
      .toISOString()
      .slice(0, 10)
    return { date, bucket: buckets.find((b) => b.day.slice(0, 10) === date) }
  })
  const max = Math.max(1, ...buckets.map((b) => b.p95_ms ?? 0))
  const maxCount = Math.max(1, ...buckets.map((b) => b.executions))
  const x = (i: number) => 60 + (i * 820) / Math.max(1, data.length - 1)
  const y = (n: number) => 180 - (n / max) * 140
  return (
    <Card>
      <CardHeader>
        <CardTitle>Duration and volume</CardTitle>
        <p className="text-sm text-muted-foreground">
          Daily UTC buckets · p50 solid / p95 dashed · successful server
          operations. First and last days may be partial.
        </p>
      </CardHeader>
      <CardContent>
        <svg
          viewBox="0 0 940 285"
          className="w-full"
          role="img"
          aria-label="Daily p50 and p95 duration with recorded execution volume. Exact values in the table below."
        >
          <text x="0" y="35" className="fill-muted-foreground text-xs">
            {ms(max)}
          </text>
          <text x="0" y="183" className="fill-muted-foreground text-xs">
            0 ms
          </text>
          <path d="M60 180H900" className="stroke-border" fill="none" />
          {(["p50_ms", "p95_ms"] as const).map((key) => (
            <path
              key={key}
              d={data
                .map((d, i) =>
                  d.bucket?.[key] == null
                    ? ""
                    : `${i && data[i - 1].bucket?.[key] != null ? "L" : "M"}${x(i)},${y(d.bucket[key]!)}`
                )
                .join(" ")}
              fill="none"
              className={
                key === "p50_ms" ? "stroke-primary" : "stroke-muted-foreground"
              }
              strokeWidth="2"
              strokeDasharray={key === "p95_ms" ? "5 4" : undefined}
            />
          ))}
          {data.map((d, i) => (
            <g key={d.date}>
              {d.bucket?.p50_ms != null && (
                <circle
                  cx={x(i)}
                  cy={y(d.bucket.p50_ms)}
                  r="3"
                  className="fill-primary"
                >
                  <title>
                    {d.date}: p50 {ms(d.bucket.p50_ms)}, p95{" "}
                    {ms(d.bucket.p95_ms)}, {d.bucket.successes} successful
                    samples
                  </title>
                </circle>
              )}
              <rect
                x={x(i) - 5}
                y={245 - ((d.bucket?.executions ?? 0) / maxCount) * 40}
                width="10"
                height={((d.bucket?.executions ?? 0) / maxCount) * 40}
                className="fill-primary/40"
              >
                <title>
                  {d.date}: {d.bucket?.executions ?? 0} recorded operations
                </title>
              </rect>
            </g>
          ))}
          <text x="60" y="270" className="fill-muted-foreground text-xs">
            {data[0].date}
          </text>
          <text
            x="880"
            y="270"
            textAnchor="end"
            className="fill-muted-foreground text-xs"
          >
            {data.at(-1)?.date}
          </text>
        </svg>
        <details>
          <summary className="cursor-pointer text-sm text-muted-foreground">
            Daily values and sample counts
          </summary>
          <Table>
            <TableHeader>
              <TableRow>
                {[
                  "UTC date",
                  "Executions",
                  "Successful samples",
                  "p50",
                  "p95",
                  "Failed / rejected",
                ].map((h) => (
                  <TableHead key={h}>{h}</TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.map(({ date, bucket: b }) => (
                <TableRow key={date}>
                  <TableCell>{date}</TableCell>
                  <TableCell>{b?.executions ?? 0}</TableCell>
                  <TableCell>{b?.successes ?? 0}</TableCell>
                  <TableCell>{ms(b?.p50_ms ?? null)}</TableCell>
                  <TableCell>{ms(b?.p95_ms ?? null)}</TableCell>
                  <TableCell>{b?.failures ?? 0}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </details>
      </CardContent>
    </Card>
  )
}
function Counts({ title, rows }: { title: string; rows: QueryCount[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {rows.length ? (
          <ul className="space-y-2">
            {rows.map((r) => (
              <li key={r.name} className="flex justify-between gap-4 text-sm">
                <span className="font-mono break-all">{r.name}</span>
                <span className="tabular-nums">{number(r.count)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">No recorded activity.</p>
        )}
      </CardContent>
    </Card>
  )
}
export function QueriesPage() {
  const [days, setDays] = useState("7")
  const [source, setSource] = useState("public")
  const [operation, setOperation] = useState("execute")
  const [sort, setSort] = useState("executions")
  const [pattern, setPattern] = useState<string | null>(null)
  const [offset, setOffset] = useState(0)
  const [executionOffset, setExecutionOffset] = useState(0)
  const [selected, setSelected] = useState<string | null>(null)
  const params = new URLSearchParams({
    days,
    source,
    operation,
    sort,
    offset: String(offset),
  })
  if (pattern) params.set("pattern", pattern)
  const query = useQuery({
    queryKey: ["query-history", params.toString()],
    queryFn: ({ signal }) =>
      read<QueryDashboard>(`/query-history?${params}`, signal),
    refetchInterval: 60_000,
  })
  const listParams = new URLSearchParams(params)
  listParams.delete("sort")
  listParams.set("offset", String(executionOffset))
  const executions = useQuery({
    queryKey: ["query-history-executions", listParams.toString()],
    queryFn: ({ signal }) =>
      read<QueryExecutionPage>(
        `/query-history/executions?${listParams}`,
        signal
      ),
    refetchInterval: 60_000,
  })
  const detail = useQuery({
    queryKey: ["query-history-detail", selected],
    enabled: selected !== null,
    queryFn: ({ signal }) =>
      read<QueryExecution>(`/query-history/executions/${selected}`, signal),
    gcTime: 0,
  })
  const reset = () => {
    setPattern(null)
    setOffset(0)
    setExecutionOffset(0)
  }
  const data = query.data
  return (
    <div className="space-y-5 pb-8">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Queries</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Common patterns, execution latency and failures. Private history
            retained for 30 days.
          </p>
        </div>
        <Button
          variant="outline"
          onClick={() => {
            void query.refetch()
            void executions.refetch()
          }}
        >
          Refresh
        </Button>
      </div>
      <div className="flex flex-wrap gap-3">
        <Filter
          label="Time range"
          value={days}
          values={[
            ["1", "Last 24 hours"],
            ["7", "Last 7 days"],
            ["30", "Last 30 days"],
          ]}
          change={(v) => {
            setDays(v)
            reset()
          }}
        />
        <Filter
          label="Source"
          value={source}
          values={[
            ["public", "Public / SDK"],
            ["all", "All sources"],
            ["public_console", "Public console"],
            ["assistant", "Assistant"],
            ["sdk", "SDK"],
            ["admin", "Admin"],
            ["internal", "Internal"],
            ["unknown", "Unknown"],
          ]}
          change={(v) => {
            setSource(v)
            reset()
          }}
        />
        <Filter
          label="Operation"
          value={operation}
          values={[
            ["execute", "Execution"],
            ["prepare", "Preparation"],
          ]}
          change={(v) => {
            setOperation(v)
            reset()
          }}
        />
      </div>
      <p className="text-xs text-muted-foreground">
        Best-effort server history, not visitor counts. Direct DuckLake queries
        and requests rejected before SQL admission are not recorded. Duration
        excludes history delivery and browser transport.
      </p>
      {pattern && (
        <div className="flex items-center gap-3">
          <Badge variant="secondary">
            {pattern === "unparsed" ? "Unparsed queries" : "Selected pattern"}
          </Badge>
          <Button variant="ghost" onClick={reset}>
            Show all patterns
          </Button>
        </div>
      )}
      {query.isPending && <p role="status">Loading query history…</p>}
      {query.error && (
        <p role="alert" className="text-destructive">
          {extractApiError(query.error)}
        </p>
      )}
      {data && (
        <>
          <Summary stats={data.summary} />
          {data.summary.executions === 0 ? (
            <Card>
              <CardContent className="py-8">
                <h2 className="font-medium">
                  No recorded queries in this view
                </h2>
                <p className="mt-2 text-sm text-muted-foreground">
                  Try another source or time range. History starts after the
                  backend migration and deployment; earlier queries are not
                  backfilled.
                </p>
              </CardContent>
            </Card>
          ) : (
            <>
              <Trend buckets={data.trend} days={Number(days)} />
              <Card>
                <CardHeader className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <CardTitle>
                      {pattern ? "Pattern detail" : "Query patterns"}
                    </CardTitle>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {number(data.pattern_count)} patterns · percentiles use
                      successful samples
                    </p>
                  </div>
                  <Filter
                    label="Rank by"
                    value={sort}
                    values={[
                      ["executions", "Most used"],
                      ["failures", "Most failures"],
                      ["p95", "Highest p95"],
                      ["total", "Most total time"],
                    ]}
                    change={(v) => {
                      setSort(v)
                      setOffset(0)
                    }}
                  />
                </CardHeader>
                <CardContent>
                  <Table>
                    <TableHeader>
                      <TableRow>
                        {[
                          "Pattern / sources",
                          "Executions / share",
                          "p50",
                          "p95 / samples",
                          "Failed / rejected",
                          "Total time",
                          "Last seen",
                        ].map((h) => (
                          <TableHead key={h}>{h}</TableHead>
                        ))}
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {data.patterns.map((p) => (
                        <TableRow key={p.pattern_key}>
                          <TableCell className="max-w-md">
                            <button
                              className="w-full cursor-pointer text-left hover:underline"
                              onClick={() => {
                                setPattern(p.pattern_key)
                                setOffset(0)
                                setExecutionOffset(0)
                              }}
                            >
                              <pre
                                className={`font-mono text-xs break-all whitespace-pre-wrap ${pattern ? "" : "line-clamp-3"}`}
                              >
                                {p.query_template ??
                                  "Unparsed SQL (no normalized pattern)"}
                              </pre>
                            </button>
                            <p className="mt-1 text-xs text-muted-foreground">
                              {p.sources.join(", ")}
                            </p>
                          </TableCell>
                          <TableCell>
                            {number(p.executions)}
                            <p className="text-xs text-muted-foreground">
                              {percent(p.executions, data.summary.executions)}
                            </p>
                          </TableCell>
                          <TableCell>{ms(p.p50_ms)}</TableCell>
                          <TableCell>
                            {ms(p.p95_ms)}
                            <p className="text-xs text-muted-foreground">
                              {number(p.successes)}
                              {p.successes < 20 ? " · small sample" : ""}
                            </p>
                          </TableCell>
                          <TableCell>
                            {number(p.failures)}
                            <p className="text-xs text-muted-foreground">
                              {percent(p.failures, p.executions)}
                            </p>
                          </TableCell>
                          <TableCell>{ms(p.total_ms)}</TableCell>
                          <TableCell className="text-xs whitespace-nowrap">
                            {stamp(p.last_seen)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                  <div className="mt-3 flex justify-end gap-2">
                    <Button
                      variant="outline"
                      disabled={!offset}
                      onClick={() => setOffset(Math.max(0, offset - 50))}
                    >
                      Previous
                    </Button>
                    <Button
                      variant="outline"
                      disabled={offset + 50 >= data.pattern_count}
                      onClick={() => setOffset(offset + 50)}
                    >
                      Next patterns
                    </Button>
                  </div>
                </CardContent>
              </Card>
              <div className="grid gap-4 lg:grid-cols-3">
                <Counts title="Failure reasons" rows={data.failures} />
                <Counts title="Relations used" rows={data.relations} />
                <Counts title="Functions used" rows={data.functions} />
              </div>
            </>
          )}
        </>
      )}
      <Card>
        <CardHeader>
          <CardTitle>Recent executions</CardTitle>
          <p className="text-xs text-muted-foreground">
            Select an execution to inspect its exact SQL, parameters and
            outcome.
          </p>
        </CardHeader>
        <CardContent>
          {executions.error && (
            <p role="alert">{extractApiError(executions.error)}</p>
          )}
          {executions.isPending && <p role="status">Loading executions…</p>}
          <Table>
            <TableHeader>
              <TableRow>
                {[
                  "Started",
                  "Source",
                  "Outcome",
                  "Duration",
                  "Rows",
                  "Result",
                ].map((h) => (
                  <TableHead key={h}>{h}</TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {executions.data?.executions.map((e) => (
                <TableRow key={e.execution_id}>
                  <TableCell>
                    <Button
                      variant="link"
                      onClick={() => setSelected(e.execution_id)}
                    >
                      {stamp(e.started_at)}
                    </Button>
                  </TableCell>
                  <TableCell>{e.source}</TableCell>
                  <TableCell>
                    <Badge
                      variant={
                        e.outcome === "success" ? "secondary" : "outline"
                      }
                    >
                      {e.outcome}
                    </Badge>
                    {e.error_code && <p className="text-xs">{e.error_code}</p>}
                  </TableCell>
                  <TableCell>{ms(e.elapsed_ms)}</TableCell>
                  <TableCell>{e.result_rows ?? "—"}</TableCell>
                  <TableCell>
                    {e.truncated === null
                      ? "—"
                      : e.truncated
                        ? "Truncated"
                        : "Complete"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {!executions.isPending &&
            !executions.error &&
            !executions.data?.executions.length && (
              <p className="py-4 text-sm text-muted-foreground">
                No executions in this view.
              </p>
            )}
          <div className="mt-3 flex justify-end gap-2">
            <Button
              variant="outline"
              disabled={!executionOffset}
              onClick={() =>
                setExecutionOffset(Math.max(0, executionOffset - 50))
              }
            >
              Previous
            </Button>
            <Button
              variant="outline"
              disabled={!executions.data?.has_more}
              onClick={() => setExecutionOffset(executionOffset + 50)}
            >
              Next executions
            </Button>
          </div>
        </CardContent>
      </Card>
      <Sheet
        open={selected !== null}
        onOpenChange={(open) => {
          if (!open) setSelected(null)
        }}
      >
        <SheetContent className="w-full overflow-y-auto sm:max-w-3xl">
          <SheetHeader>
            <SheetTitle>Query execution</SheetTitle>
            <SheetDescription>
              Private SQL and parameters. Automatically removed with the
              execution after 30 days.
            </SheetDescription>
          </SheetHeader>
          <div className="space-y-5 p-5">
            {detail.isPending && <p role="status">Loading execution…</p>}
            {detail.error && (
              <p role="alert">{extractApiError(detail.error)}</p>
            )}
            {detail.data && (
              <>
                <dl className="grid grid-cols-2 gap-3 text-sm">
                  {Object.entries({
                    ID: detail.data.execution_id,
                    Request: detail.data.request_id,
                    Source: detail.data.source,
                    Operation: detail.data.operation,
                    Outcome: detail.data.outcome,
                    Error: detail.data.error_code,
                    Started: stamp(detail.data.started_at),
                    Duration: ms(detail.data.elapsed_ms),
                    Rows: detail.data.result_rows,
                    "Result JSON bytes": detail.data.result_bytes,
                    Truncated: detail.data.truncated,
                    Snapshot: detail.data.source_snapshot,
                    Version: detail.data.service_version,
                  }).map(([k, v]) => (
                    <div key={k}>
                      <dt className="text-muted-foreground">{k}</dt>
                      <dd className="break-all">
                        {v == null ? "—" : String(v)}
                      </dd>
                    </div>
                  ))}
                </dl>
                {[
                  ["Original SQL", detail.data.sql_text],
                  [
                    "Parameters",
                    JSON.stringify(detail.data.parameters, null, 2),
                  ],
                  [
                    "Template",
                    detail.data.query_template ?? "Normalization unavailable",
                  ],
                  ["Structure", JSON.stringify(detail.data.features, null, 2)],
                ].map(([title, value]) => (
                  <section key={title}>
                    <h3 className="mb-2 font-medium">{title}</h3>
                    <pre className="max-h-96 overflow-auto rounded-md bg-muted p-3 text-xs break-all whitespace-pre-wrap">
                      {value}
                    </pre>
                  </section>
                ))}
              </>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </div>
  )
}
