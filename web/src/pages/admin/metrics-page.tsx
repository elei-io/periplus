import { useMemo, useState, type ReactNode } from "react"
import {
  ActivityIcon,
  AlertTriangleIcon,
  ArrowUpRightIcon,
  CheckCircle2Icon,
  Clock3Icon,
  GaugeIcon,
  RefreshCwIcon,
  ServerIcon,
} from "lucide-react"
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts"

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
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart"
import { Progress } from "@/components/ui/progress"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useOperationsMetrics } from "@/hooks/use-operations-metrics"
import { extractApiError } from "@/lib/api"
import { cn } from "@/lib/utils"
import type {
  OperationsMetricsResponse,
  PermitMetrics,
  TaskStateMetrics,
} from "@/types/operations"

const windows = [
  { value: 900, label: "15 minutes" },
  { value: 3600, label: "1 hour" },
  { value: 21600, label: "6 hours" },
  { value: 86400, label: "24 hours" },
]

const taskChartConfig = {
  succeeded: { label: "Succeeded", color: "var(--chart-2)" },
  failed: { label: "Failed", color: "var(--destructive)" },
  cancelled: { label: "Cancelled", color: "var(--chart-1)" },
} satisfies ChartConfig

const domainChartConfig = {
  duration: { label: "p95 seconds", color: "var(--chart-3)" },
} satisfies ChartConfig

export function MetricsPage() {
  const [windowSeconds, setWindowSeconds] = useState(21600)
  const metricsQuery = useOperationsMetrics(windowSeconds)
  const metrics = metricsQuery.data

  const queued =
    metrics?.cluster.tasks.reduce((total, task) => total + task.queued, 0) ?? 0
  const browserPermit = metrics?.cluster.permits.find(
    (permit) => permit.scope === "browser"
  )
  const policyPermits =
    metrics?.cluster.permits.filter((permit) => permit.scope === "policy") ?? []
  const slowDomains = useMemo(() => {
    return [...(metrics?.domains ?? [])]
      .sort(
        (left, right) => right.duration_p95_seconds - left.duration_p95_seconds
      )
      .slice(0, 8)
      .map((domain) => ({
        name: domain.domain,
        duration: domain.duration_p95_seconds,
      }))
  }, [metrics?.domains])

  if (metricsQuery.isLoading && !metrics) {
    return <MetricsPageSkeleton />
  }

  if (metricsQuery.isError && !metrics) {
    return (
      <div className="flex w-full items-center justify-center">
        <Card className="w-full max-w-lg">
          <CardHeader>
            <CardTitle>Metrics are unavailable</CardTitle>
            <CardDescription>
              {extractApiError(metricsQuery.error)}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button onClick={() => void metricsQuery.refetch()}>
              <RefreshCwIcon />
              Retry
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  if (!metrics) {
    return null
  }

  const browserUtilization = ratio(
    browserPermit?.in_use ?? 0,
    browserPermit?.capacity ?? 0
  )

  return (
    <div className="flex min-h-0 w-full flex-col overflow-y-auto pr-1">
      <div className="mx-auto flex w-full max-w-[96rem] flex-col gap-4 pb-8">
        <section className="flex flex-wrap items-start justify-between gap-3 border-b pb-4">
          <div>
            <div className="flex items-center gap-2">
              <ActivityIcon className="size-4 text-muted-foreground" />
              <h1 className="text-lg font-medium">Atlas operations</h1>
              <HealthBadge metrics={metrics} />
            </div>
            <p className="mt-1 max-w-2xl text-xs/relaxed text-muted-foreground">
              Current infrastructure pressure and recent task and crawl health.
              Prometheus remains the source for full history and alerting.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Select
              value={String(windowSeconds)}
              onValueChange={(value) => setWindowSeconds(Number(value))}
            >
              <SelectTrigger aria-label="Metrics window" className="w-36">
                {
                  windows.find((window) => window.value === windowSeconds)
                    ?.label
                }
              </SelectTrigger>
              <SelectContent>
                {windows.map((window) => (
                  <SelectItem key={window.value} value={String(window.value)}>
                    {window.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              variant="outline"
              size="sm"
              disabled={metricsQuery.isFetching}
              onClick={() => void metricsQuery.refetch()}
            >
              <RefreshCwIcon
                className={cn(metricsQuery.isFetching && "animate-spin")}
              />
              Refresh
            </Button>
          </div>
        </section>

        <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          <PostureCard
            label="Workers"
            value={String(metrics.cluster.workers_live)}
            detail={`${metrics.cluster.worker_active_runs} of ${metrics.cluster.worker_capacity} slots active`}
            icon={ServerIcon}
            warning={metrics.cluster.workers_stale > 0}
            warningDetail={
              metrics.cluster.workers_stale > 0
                ? `${metrics.cluster.workers_stale} stale ${metrics.cluster.workers_stale === 1 ? "worker" : "workers"}`
                : undefined
            }
          />
          <PostureCard
            label="Queued work"
            value={String(queued)}
            detail={`Oldest ${formatDuration(metrics.cluster.oldest_queued_age_seconds)}`}
            icon={Clock3Icon}
            warning={queued > 0 && metrics.cluster.worker_capacity === 0}
          />
          <PostureCard
            label="Browser capacity"
            value={`${Math.round(browserUtilization)}%`}
            detail={`${browserPermit?.in_use ?? 0} of ${browserPermit?.capacity ?? 0} permits`}
            icon={GaugeIcon}
            warning={browserUtilization >= 90}
          />
          <PostureCard
            label="Task success"
            value={formatPercent(metrics.tasks.success_ratio)}
            detail={`${metrics.tasks.terminal_runs} terminal runs`}
            icon={CheckCircle2Icon}
            warning={
              metrics.tasks.terminal_runs > 0 &&
              metrics.tasks.success_ratio < 0.98
            }
          />
          <PostureCard
            label="Crawl success"
            value={formatPercent(metrics.crawls.success_ratio)}
            detail={`${metrics.crawls.total} persisted crawls`}
            icon={ActivityIcon}
            warning={
              metrics.crawls.total > 0 && metrics.crawls.success_ratio < 0.98
            }
          />
        </section>

        <section className="grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(20rem,0.6fr)]">
          <TaskOutcomesCard tasks={metrics.cluster.tasks} />
          <Card>
            <CardHeader>
              <CardTitle>Latency posture</CardTitle>
              <CardDescription>
                Median and tail latency in the selected window.
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-5">
              <LatencyRow
                label="Queue wait"
                median={metrics.tasks.queue_p50_seconds}
                tail={metrics.tasks.queue_p95_seconds}
              />
              <LatencyRow
                label="Task execution"
                median={metrics.tasks.execution_p50_seconds}
                tail={metrics.tasks.execution_p95_seconds}
              />
              <LatencyRow
                label="Crawl navigation"
                median={metrics.crawls.duration_p50_seconds}
                tail={metrics.crawls.duration_p95_seconds}
              />
              {metrics.prometheus.available ? (
                <LatencyRow
                  label="Capacity wait"
                  median={null}
                  tail={metrics.prometheus.capacity_wait_p95_seconds}
                />
              ) : null}
            </CardContent>
          </Card>
        </section>

        <section className="grid gap-4 xl:grid-cols-2">
          <DomainLatencyCard domains={slowDomains} />
          <FailureCard metrics={metrics} />
        </section>

        <section className="grid gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(20rem,0.65fr)]">
          <CapacityTable permits={policyPermits} />
          <PrometheusCard prometheus={metrics.prometheus} />
        </section>
      </div>
    </div>
  )
}

function HealthBadge({ metrics }: { metrics: OperationsMetricsResponse }) {
  const unhealthy =
    metrics.cluster.workers_stale > 0 ||
    (metrics.cluster.tasks.some((task) => task.queued > 0) &&
      metrics.cluster.worker_capacity === 0)
  return (
    <Badge variant={unhealthy ? "destructive" : "secondary"}>
      {unhealthy ? "Needs attention" : "Operational"}
    </Badge>
  )
}

function PostureCard({
  label,
  value,
  detail,
  icon: Icon,
  warning = false,
  warningDetail,
}: {
  label: string
  value: string
  detail: string
  icon: typeof ActivityIcon
  warning?: boolean
  warningDetail?: string
}) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardAction>
          <Icon
            className={cn(
              "size-4 text-muted-foreground",
              warning && "text-destructive"
            )}
          />
        </CardAction>
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-medium tabular-nums">{value}</div>
        <p
          className={cn(
            "mt-1 text-muted-foreground",
            warning && !warningDetail && "text-destructive"
          )}
        >
          {detail}
        </p>
        {warningDetail ? (
          <p className="mt-1 text-destructive">{warningDetail}</p>
        ) : null}
      </CardContent>
    </Card>
  )
}

function TaskOutcomesCard({ tasks }: { tasks: TaskStateMetrics[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Task outcomes</CardTitle>
        <CardDescription>
          Terminal runs by primitive in the selected window.
        </CardDescription>
        <CardAction>
          <a
            className="inline-flex items-center gap-1 text-link hover:underline"
            href="/scheduled-work/tasks"
          >
            Tasks <ArrowUpRightIcon className="size-3" />
          </a>
        </CardAction>
      </CardHeader>
      <CardContent>
        <ChartContainer
          config={taskChartConfig}
          className="aspect-auto h-64 w-full"
        >
          <BarChart
            accessibilityLayer
            data={tasks}
            margin={{ left: 0, right: 8 }}
          >
            <CartesianGrid vertical={false} />
            <XAxis dataKey="primitive" tickLine={false} axisLine={false} />
            <YAxis
              allowDecimals={false}
              tickLine={false}
              axisLine={false}
              width={28}
            />
            <ChartTooltip content={<ChartTooltipContent />} />
            <ChartLegend content={<ChartLegendContent />} />
            <Bar
              dataKey="succeeded"
              stackId="outcomes"
              fill="var(--color-succeeded)"
              radius={[0, 0, 2, 2]}
            />
            <Bar
              dataKey="failed"
              stackId="outcomes"
              fill="var(--color-failed)"
            />
            <Bar
              dataKey="cancelled"
              stackId="outcomes"
              fill="var(--color-cancelled)"
              radius={[2, 2, 0, 0]}
            />
          </BarChart>
        </ChartContainer>
      </CardContent>
    </Card>
  )
}

function DomainLatencyCard({
  domains,
}: {
  domains: Array<{ name: string; duration: number }>
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Slow domains</CardTitle>
        <CardDescription>
          p95 crawl navigation latency by active domain.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {domains.length ? (
          <ChartContainer
            config={domainChartConfig}
            className="aspect-auto h-64 w-full"
          >
            <BarChart
              accessibilityLayer
              data={domains}
              layout="vertical"
              margin={{ left: 8, right: 16 }}
            >
              <CartesianGrid horizontal={false} />
              <XAxis type="number" tickLine={false} axisLine={false} unit="s" />
              <YAxis
                dataKey="name"
                type="category"
                tickLine={false}
                axisLine={false}
                width={112}
                tick={{ fontSize: 11 }}
              />
              <ChartTooltip content={<ChartTooltipContent />} />
              <Bar dataKey="duration" fill="var(--color-duration)" radius={3} />
            </BarChart>
          </ChartContainer>
        ) : (
          <EmptyState>No crawl latency data in this window.</EmptyState>
        )}
      </CardContent>
    </Card>
  )
}

function FailureCard({ metrics }: { metrics: OperationsMetricsResponse }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Failure pressure</CardTitle>
        <CardDescription>Normalized persisted crawl failures.</CardDescription>
      </CardHeader>
      <CardContent>
        {metrics.failures.length ? (
          <div className="grid gap-3">
            {metrics.failures.map((failure) => (
              <div key={failure.reason} className="flex items-center gap-3">
                <AlertTriangleIcon className="size-3.5 text-destructive" />
                <span className="flex-1 capitalize">
                  {failure.reason.replaceAll("_", " ")}
                </span>
                <span className="font-medium tabular-nums">
                  {failure.count}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState>No persisted crawl failures in this window.</EmptyState>
        )}
      </CardContent>
    </Card>
  )
}

function CapacityTable({ permits }: { permits: PermitMetrics[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>CrawlPolicy capacity</CardTitle>
        <CardDescription>
          Current deployment-wide policy permit utilization.
        </CardDescription>
        <CardAction>
          <a
            className="inline-flex items-center gap-1 text-link hover:underline"
            href="/settings/crawl-policies"
          >
            Policies <ArrowUpRightIcon className="size-3" />
          </a>
        </CardAction>
      </CardHeader>
      <CardContent>
        <Table containerClassName="max-h-72 rounded-md border">
          <TableHeader>
            <TableRow>
              <TableHead>Policy</TableHead>
              <TableHead>Utilization</TableHead>
              <TableHead className="w-24 text-right">Permits</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {permits.map((permit) => {
              const utilization = ratio(permit.in_use, permit.capacity)
              const policyLabel = permit.match ?? permit.policy
              return (
                <TableRow key={permit.policy}>
                  <TableCell className="max-w-80 truncate font-mono text-xs" title={policyLabel}>
                    {policyLabel}
                  </TableCell>
                  <TableCell>
                    <Progress
                      value={utilization}
                      aria-label={`${policyLabel} utilization`}
                    />
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {permit.in_use} / {permit.capacity}
                  </TableCell>
                </TableRow>
              )
            })}
            {permits.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={3}
                  className="h-20 text-center text-muted-foreground"
                >
                  No CrawlPolicies currently define concurrency limits.
                </TableCell>
              </TableRow>
            ) : null}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

function PrometheusCard({
  prometheus,
}: {
  prometheus: OperationsMetricsResponse["prometheus"]
}) {
  const status = !prometheus.configured
    ? "Not configured"
    : prometheus.available
      ? "Connected"
      : "Unavailable"
  return (
    <Card>
      <CardHeader>
        <CardTitle>Prometheus</CardTitle>
        <CardDescription>
          Time-series enrichment for live operational signals.
        </CardDescription>
        <CardAction>
          <Badge variant={prometheus.available ? "secondary" : "outline"}>
            {status}
          </Badge>
        </CardAction>
      </CardHeader>
      <CardContent className="grid gap-4">
        {prometheus.available ? (
          <>
            <MetricPair
              label="Capacity waiters"
              value={formatNumber(prometheus.capacity_waiters)}
            />
            <MetricPair
              label="Capacity wait p95"
              value={formatDuration(prometheus.capacity_wait_p95_seconds)}
            />
            <MetricPair
              label="Page acquisitions"
              value={`${formatNumber(prometheus.page_acquisitions_per_second, 2)}/s`}
            />
            <MetricPair
              label="Page success"
              value={formatPercent(prometheus.page_success_ratio)}
            />
            <MetricPair
              label="Dropped observations"
              value={formatNumber(prometheus.dropped_observations)}
              warning={(prometheus.dropped_observations ?? 0) > 0}
            />
          </>
        ) : (
          <p className="text-xs/relaxed text-muted-foreground">
            {prometheus.configured
              ? "Atlas could not reach the configured Prometheus server. Current Postgres-backed metrics remain available."
              : "Set ATLAS_PROMETHEUS_URL on the API to add capacity waiters, histogram percentiles, and acquisition rates."}
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function LatencyRow({
  label,
  median,
  tail,
}: {
  label: string
  median: number | null
  tail: number | null
}) {
  return (
    <div className="grid grid-cols-[1fr_auto_auto] items-baseline gap-4 border-b pb-3 last:border-0 last:pb-0">
      <span>{label}</span>
      <span className="text-muted-foreground tabular-nums">
        p50 {formatDuration(median)}
      </span>
      <span className="font-medium tabular-nums">
        p95 {formatDuration(tail)}
      </span>
    </div>
  )
}

function MetricPair({
  label,
  value,
  warning = false,
}: {
  label: string
  value: string
  warning?: boolean
}) {
  return (
    <div className="flex items-center justify-between gap-4 border-b pb-3 last:border-0 last:pb-0">
      <span className="text-muted-foreground">{label}</span>
      <span
        className={cn(
          "font-medium tabular-nums",
          warning && "text-destructive"
        )}
      >
        {value}
      </span>
    </div>
  )
}

function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-52 items-center justify-center text-muted-foreground">
      {children}
    </div>
  )
}

function MetricsPageSkeleton() {
  return (
    <div className="flex w-full flex-col gap-4">
      <Skeleton className="h-16 w-full" />
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        {Array.from({ length: 5 }).map((_, index) => (
          <Skeleton key={index} className="h-28" />
        ))}
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <Skeleton className="h-80" />
        <Skeleton className="h-80" />
      </div>
    </div>
  )
}

function ratio(value: number, capacity: number) {
  return capacity > 0 ? Math.min(100, (value / capacity) * 100) : 0
}

function formatPercent(value: number | null) {
  return value === null ? "—" : `${(value * 100).toFixed(1)}%`
}

function formatNumber(value: number | null, digits = 0) {
  return value === null ? "—" : value.toFixed(digits)
}

function formatDuration(value: number | null) {
  if (value === null) {
    return "—"
  }
  if (value < 1) {
    return `${Math.round(value * 1000)}ms`
  }
  if (value < 60) {
    return `${value.toFixed(1)}s`
  }
  return `${Math.floor(value / 60)}m ${Math.round(value % 60)}s`
}
