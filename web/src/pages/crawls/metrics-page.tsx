import { useMemo, useState } from "react"
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipContentProps,
} from "recharts"
import {
  ArrowUpRightIcon,
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  useCrawlConcurrencyLimits,
  useGraphRunMaterializationLag,
  useGraphRuns,
  usePolicyPressure,
} from "@/hooks/use-crawl-graphs"
import { useCrawlPolicies } from "@/hooks/use-resource-data"
import type {
  CrawlConcurrencyLimits,
  GraphRunMaterializationLag,
  GraphRunRecord,
  PolicyPressureHours,
  PolicyPressureResponse,
} from "@/types/graphs"
import type { CrawlPolicyRecord } from "@/types/resources"

const policyFilters = {
  matchPattern: "",
  enabled: "enabled" as const,
  template: "",
  mode: "all" as const,
}

const pressureRanges: PolicyPressureHours[] = [1, 6, 24, 72]
const chartColors = [
  "var(--chart-2)",
  "var(--chart-1)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
]

export function CrawlMetricsPage() {
  const [pressureHours, setPressureHours] = useState<PolicyPressureHours>(24)
  const runsQuery = useGraphRuns()
  const concurrencyQuery = useCrawlConcurrencyLimits()
  const materializationLagQuery = useGraphRunMaterializationLag()
  const pressureQuery = usePolicyPressure(pressureHours)
  const policiesQuery = useCrawlPolicies(policyFilters, {
    limit: 500,
    offset: 0,
  })
  const policies = (policiesQuery.data?.items ?? []).filter(
    (policy) => policy.concurrency !== null,
  )
  const lag = materializationLagQuery.data?.items ?? []
  const lagByRun = new Map(lag.map((item) => [item.run_id, item]))
  const runs = runsQuery.data?.items ?? []

  if (runsQuery.isLoading || materializationLagQuery.isLoading) {
    return (
      <LoaderCircleIcon className="m-auto size-5 animate-spin text-muted-foreground" />
    )
  }

  return (
    <div className="flex w-full min-w-0 flex-col gap-4 pb-2">
      <LiveTotals
        concurrency={concurrencyQuery.data}
        policies={policies}
        runs={runs}
        lag={lag}
      />

      <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(18rem,1fr)]">
        <PolicyPressureChart
          hours={pressureHours}
          onHoursChange={setPressureHours}
          pressure={pressureQuery.data}
          policies={policies}
          loading={pressureQuery.isLoading || policiesQuery.isLoading}
        />
        <ResourceHeadroom
          concurrency={concurrencyQuery.data}
          policies={policies}
          lag={lag}
        />
      </div>

      <LatestRuns runs={runs} lagByRun={lagByRun} />
    </div>
  )
}

function LiveTotals({
  concurrency,
  policies,
  runs,
  lag,
}: {
  concurrency?: CrawlConcurrencyLimits
  policies: CrawlPolicyRecord[]
  runs: GraphRunRecord[]
  lag: GraphRunMaterializationLag[]
}) {
  const policyLimits = policyLimitsByGroup(policies)
  const remoteByGroup = resourceUseByPrefix(concurrency, "remote:")
  const busiest = [...policyLimits].sort(([leftGroup, leftLimit], [rightGroup, rightLimit]) => {
    const left = (remoteByGroup.get(leftGroup) ?? 0) / leftLimit
    const right = (remoteByGroup.get(rightGroup) ?? 0) / rightLimit
    return right - left
  })[0]
  const pendingUpdates = lag.reduce((sum, item) => sum + item.pending_updates, 0)
  const cooling = runs.filter((run) => {
    const runLag = lag.find((item) => item.run_id === run.id)
    return isTerminalRun(run) && Boolean(runLag?.pending_updates)
  }).length
  const busiestUsed = busiest ? remoteByGroup.get(busiest[0]) ?? 0 : 0

  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <MetricCard
        label="Network fetches"
        value={`${concurrency?.runtime_active ?? 0} / ${concurrency?.runtime_capacity ?? 0}`}
        detail="active worker slots"
      />
      <MetricCard
        label="Busiest website limit"
        value={busiest ? `${busiestUsed} / ${busiest[1]}` : "—"}
        detail={busiest ? policyGroupLabel(busiest[0], policies) : "no policy limits"}
      />
      <MetricCard
        label="View updates waiting"
        value={pendingUpdates.toLocaleString()}
        detail="across all graph runs"
        attention={lag.some((item) => item.failed_updates > 0)}
      />
      <MetricCard
        label="Runs cooling down"
        value={cooling.toLocaleString()}
        detail="crawl done, views catching up"
      />
    </div>
  )
}

function MetricCard({
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
    <Card size="sm">
      <CardContent>
        <p className="text-xs font-medium text-muted-foreground">{label}</p>
        <p
          className={`mt-1 text-2xl font-semibold tabular-nums ${attention ? "text-destructive" : ""}`}
        >
          {value}
        </p>
        <p className="mt-0.5 text-xs text-muted-foreground">{detail}</p>
      </CardContent>
    </Card>
  )
}

type PressureSeries = {
  key: string
  countKey: string
  domainGroup: string
  label: string
  limit: number
  color: string
}

type PressureDatum = Record<string, number> & { timestamp: number }

function PolicyPressureChart({
  hours,
  onHoursChange,
  pressure,
  policies,
  loading,
}: {
  hours: PolicyPressureHours
  onHoursChange: (hours: PolicyPressureHours) => void
  pressure?: PolicyPressureResponse
  policies: CrawlPolicyRecord[]
  loading: boolean
}) {
  const { data, series } = useMemo(
    () => pressureChartData(pressure, policies),
    [pressure, policies],
  )

  return (
    <Card className="min-w-0">
      <CardHeader>
        <CardTitle>Website pressure</CardTitle>
        <CardDescription>
          Peak concurrent requests as a share of each website limit
        </CardDescription>
        <CardAction>
          <div className="flex rounded-md border bg-muted/20 p-0.5">
            {pressureRanges.map((value) => (
              <Button
                key={value}
                variant={hours === value ? "secondary" : "ghost"}
                size="xs"
                onClick={() => onHoursChange(value)}
              >
                {value}h
              </Button>
            ))}
          </div>
        </CardAction>
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="flex h-72 items-center justify-center">
            <LoaderCircleIcon className="size-5 animate-spin text-muted-foreground" />
          </div>
        ) : series.length === 0 ? (
          <div className="flex h-72 items-center justify-center rounded-md border border-dashed text-sm text-muted-foreground">
            No enabled website limits
          </div>
        ) : (
          <>
            <div className="h-72 w-full min-w-0" aria-label="Website policy pressure chart">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={data} margin={{ top: 10, right: 8, left: -12, bottom: 0 }}>
                  <CartesianGrid vertical={false} stroke="var(--border)" />
                  <XAxis
                    dataKey="timestamp"
                    type="number"
                    domain={["dataMin", "dataMax"]}
                    tickFormatter={(value) => formatChartTime(Number(value), hours)}
                    tickLine={false}
                    axisLine={false}
                    minTickGap={32}
                  />
                  <YAxis
                    domain={[0, (maximum: number) => Math.max(100, Math.ceil(maximum / 25) * 25)]}
                    tickFormatter={(value) => `${value}%`}
                    tickLine={false}
                    axisLine={false}
                    width={48}
                  />
                  <ReferenceLine
                    y={100}
                    stroke="var(--destructive)"
                    strokeDasharray="4 4"
                    label={{ value: "limit", fill: "var(--muted-foreground)", fontSize: 11 }}
                  />
                  <Tooltip
                    cursor={{ stroke: "var(--muted-foreground)", strokeDasharray: "3 3" }}
                    content={(props) => <PressureTooltip {...props} series={series} />}
                  />
                  {series.map((item) => (
                    <Area
                      key={item.key}
                      dataKey={item.key}
                      name={item.label}
                      type="stepAfter"
                      stroke={item.color}
                      fill={item.color}
                      fillOpacity={0.12}
                      strokeWidth={2}
                      isAnimationActive={false}
                    />
                  ))}
                </AreaChart>
              </ResponsiveContainer>
            </div>
            <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2 border-t pt-3">
              {series.map((item) => {
                const peak = Math.max(...data.map((datum) => datum[item.countKey] ?? 0))
                return (
                  <div key={item.key} className="flex items-center gap-2 text-xs">
                    <span
                      className="size-2 rounded-full"
                      style={{ background: item.color }}
                    />
                    <span className="font-medium">{item.label}</span>
                    <span className="text-muted-foreground tabular-nums">
                      peak {peak}/{item.limit}
                    </span>
                  </div>
                )
              })}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

function PressureTooltip({
  active,
  payload,
  label,
  series,
}: TooltipContentProps & { series: PressureSeries[] }) {
  if (!active || !payload?.length) return null
  const datum = payload[0]?.payload as PressureDatum | undefined

  return (
    <div className="min-w-44 rounded-md border bg-popover p-3 text-xs shadow-lg">
      <p className="mb-2 font-medium">{formatTooltipTime(Number(label))}</p>
      <div className="space-y-1.5">
        {series.map((item) => (
          <div key={item.key} className="flex items-center justify-between gap-5">
            <span className="flex items-center gap-2">
              <span
                className="size-2 rounded-full"
                style={{ background: item.color }}
              />
              {item.label}
            </span>
            <span className="font-medium tabular-nums">
              {datum?.[item.countKey] ?? 0} / {item.limit}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

function pressureChartData(
  pressure: PolicyPressureResponse | undefined,
  policies: CrawlPolicyRecord[],
) {
  const limits = policyLimitsByGroup(policies)
  const series: PressureSeries[] = [...limits].map(([domainGroup, limit], index) => ({
    key: `pressure_${index}`,
    countKey: `count_${index}`,
    domainGroup,
    label: policyGroupLabel(domainGroup, policies),
    limit,
    color: chartColors[index % chartColors.length],
  }))
  if (!pressure || series.length === 0) return { data: [], series }

  const pointsByGroup = new Map(
    pressure.items.map((item) => [
      item.domain_group,
      new Map(item.points.map((point) => [Date.parse(point.captured_at), point.peak_concurrency])),
    ]),
  )
  const start = Date.parse(pressure.range_start)
  const bucketMilliseconds = pressure.bucket_seconds * 1_000
  const bucketCount = Math.ceil(
    (Date.parse(pressure.range_end) - start) / bucketMilliseconds,
  )
  const data: PressureDatum[] = Array.from({ length: bucketCount }, (_, index) => {
    const timestamp = start + index * bucketMilliseconds
    const datum: PressureDatum = { timestamp }
    for (const item of series) {
      const count = pointsByGroup.get(item.domainGroup)?.get(timestamp) ?? 0
      datum[item.countKey] = count
      datum[item.key] = Math.round((count / item.limit) * 1_000) / 10
    }
    return datum
  })
  return { data, series }
}

function ResourceHeadroom({
  concurrency,
  policies,
  lag,
}: {
  concurrency?: CrawlConcurrencyLimits
  policies: CrawlPolicyRecord[]
  lag: GraphRunMaterializationLag[]
}) {
  const policyLimits = policyLimitsByGroup(policies)
  const remoteByGroup = resourceUseByPrefix(concurrency, "remote:")
  const remoteCapacity = [...policyLimits.values()].reduce((sum, value) => sum + value, 0)
  const remoteUsed = [...remoteByGroup.values()].reduce((sum, value) => sum + value, 0)
  const catalogue = concurrency?.resources.find((item) => item.name === "catalogue:hot")
  const objectRead = concurrency?.resources.find((item) => item.name === "object:read")
  const objectWrite = concurrency?.resources.find((item) => item.name === "object:write")
  const storageUsed = (objectRead?.used ?? 0) + (objectWrite?.used ?? 0)
  const storageCapacity = (objectRead?.capacity ?? 0) + (objectWrite?.capacity ?? 0)
  const pending = lag.reduce((sum, item) => sum + item.pending_updates, 0)

  return (
    <Card>
      <CardHeader>
        <CardTitle>Headroom now</CardTitle>
        <CardDescription>The first full resource is the bottleneck</CardDescription>
        <CardAction>
          <Button
            variant="ghost"
            size="sm"
            nativeButton={false}
            render={<a href="/docs" />}
          >
            Scaling guide <ArrowUpRightIcon />
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-5">
        <HeadroomRow label="Website access" used={remoteUsed} capacity={remoteCapacity} />
        <HeadroomRow
          label="Fetch workers"
          used={concurrency?.runtime_active ?? 0}
          capacity={concurrency?.runtime_capacity ?? 0}
        />
        <HeadroomRow
          label="Catalogue"
          used={catalogue?.used ?? 0}
          capacity={catalogue?.capacity ?? 0}
          note={pending ? `${pending.toLocaleString()} updates waiting` : undefined}
        />
        <HeadroomRow
          label="Object storage"
          used={storageUsed}
          capacity={storageCapacity}
        />
      </CardContent>
    </Card>
  )
}

function HeadroomRow({
  label,
  used,
  capacity,
  note,
}: {
  label: string
  used: number
  capacity: number
  note?: string
}) {
  const percent = capacity ? Math.min(100, (used / capacity) * 100) : 0
  const full = capacity > 0 && used >= capacity
  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between gap-4">
        <div>
          <p className="text-sm font-medium">{label}</p>
          {note ? <p className="text-xs text-muted-foreground">{note}</p> : null}
        </div>
        <span className={`font-medium tabular-nums ${full ? "text-destructive" : ""}`}>
          {used} / {capacity}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className={`h-full rounded-full transition-[width] duration-700 ease-out motion-reduce:transition-none ${full ? "bg-destructive" : "bg-primary"}`}
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
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
                <TableHead className="w-[7rem] text-right">Warnings</TableHead>
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
  const runFailed = run.status === "failed"
  const errorCount = run.error_count + (lag?.failed_updates ?? 0)

  return (
    <TableRow title={`Run ${run.id}`}>
      <TableCell className="py-4 pl-4 align-top whitespace-normal">
        <Badge
          variant={
            runFailed || progress.stalled
              ? "destructive"
              : coolingDown
                ? "secondary"
                : "outline"
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
          <span className="truncate">{run.graph_name || "Deleted graph"}</span>
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
            {progress.settled.toLocaleString()} / {run.request_count.toLocaleString()}
          </span>
          <span className="text-xs text-muted-foreground tabular-nums">
            {progress.activityLabel}
          </span>
        </div>
        <RunProgress run={run} className="mt-2" />
        {isActiveRun(run) && run.pending_request_count > 0 ? (
          <p className="mt-1.5 text-xs text-muted-foreground tabular-nums">
            {(run.queued_request_count ?? 0).toLocaleString()} queued ·{" "}
            {(run.fetching_request_count ?? 0).toLocaleString()} fetching ·{" "}
            {(run.processing_request_count ?? 0).toLocaleString()} ingesting / graph
          </p>
        ) : null}
      </TableCell>
      <CountCell value={run.warning_count} tone="warning" />
      <CountCell value={errorCount} tone="error" />
      <TableCell className="py-4 pr-4 align-top whitespace-normal">
        <MaterializationLag lag={lag} />
      </TableCell>
    </TableRow>
  )
}

function CountCell({ value, tone }: { value: number; tone: "warning" | "error" }) {
  return (
    <TableCell className="py-4 text-right align-top">
      <span
        className={`font-medium tabular-nums ${
          value > 0
            ? tone === "error"
              ? "text-destructive"
              : "text-amber-600 dark:text-amber-400"
            : "text-muted-foreground"
        }`}
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
        {lag.materialization_count.toLocaleString()} {lag.materialization_count === 1 ? "view" : "views"}
        {lag.failed_updates ? ` · ${lag.failed_updates.toLocaleString()} failed` : ""}
      </p>
    </div>
  )
}

function RunProgress({ run, className = "" }: { run: GraphRunRecord; className?: string }) {
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
        className="h-full rounded-full bg-primary transition-[width] duration-700 ease-out motion-reduce:transition-none"
        style={{ width: `${percent}%` }}
      />
    </div>
  )
}

function policyLimitsByGroup(policies: CrawlPolicyRecord[]) {
  const limits = new Map<string, number>()
  for (const policy of policies) {
    if (policy.concurrency === null) continue
    limits.set(policy.domain_group, Math.max(limits.get(policy.domain_group) ?? 0, policy.concurrency))
  }
  return limits
}

function policyGroupLabel(domainGroup: string, policies: CrawlPolicyRecord[]) {
  const policy = policies.find((item) => item.domain_group === domainGroup)
  return policy?.metric_slug === "system-default" ? "Default public web" : domainGroup
}

function resourceUseByPrefix(concurrency: CrawlConcurrencyLimits | undefined, prefix: string) {
  return new Map(
    (concurrency?.resources ?? [])
      .filter((resource) => resource.name.startsWith(prefix))
      .map((resource) => [resource.name.slice(prefix.length), resource.used]),
  )
}

function isActiveRun(run: GraphRunRecord) {
  return run.status === "queued" || run.status === "running"
}

function isTerminalRun(run: GraphRunRecord) {
  return !isActiveRun(run)
}

function describeRunProgress(run: GraphRunRecord) {
  const settled = Math.max(0, run.request_count - run.pending_request_count)
  const activityAt = new Date(
    run.completed_at ?? run.last_progress_at ?? run.started_at ?? run.created_at,
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

function formatChartTime(timestamp: number, hours: number) {
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: hours <= 24 ? "2-digit" : undefined,
    weekday: hours > 24 ? "short" : undefined,
  }).format(timestamp)
}

function formatTooltipTime(timestamp: number) {
  return new Intl.DateTimeFormat(undefined, {
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(timestamp)
}

function sentenceCase(value: string) {
  const label = value.replaceAll("_", " ")
  return `${label.charAt(0).toUpperCase()}${label.slice(1)}`
}
