import { ExternalLinkIcon, LoaderCircleIcon } from "lucide-react"
import type { ReactNode } from "react"

import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
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
} from "@/hooks/use-crawl-graphs"
import { useCrawlPolicies } from "@/hooks/use-resource-data"
import type {
  CrawlConcurrencyLimits,
  GraphRunMaterializationLag,
  GraphRunRecord,
} from "@/types/graphs"
import type { CrawlPolicyRecord } from "@/types/resources"

const policyFilters = {
  matchPattern: "",
  enabled: "enabled" as const,
  template: "",
  mode: "all" as const,
}

export function CrawlMetricsPage() {
  const runsQuery = useGraphRuns()
  const concurrencyQuery = useCrawlConcurrencyLimits()
  const materializationLagQuery = useGraphRunMaterializationLag()
  const policiesQuery = useCrawlPolicies(policyFilters, {
    limit: 500,
    offset: 0,
  })
  const limitedPolicies = (policiesQuery.data?.items ?? []).filter(
    (policy) => policy.concurrency !== null
  )
  const lagByRun = new Map(
    (materializationLagQuery.data?.items ?? []).map((lag) => [lag.run_id, lag])
  )

  if (runsQuery.isLoading || materializationLagQuery.isLoading) {
    return (
      <LoaderCircleIcon className="m-auto size-5 animate-spin text-muted-foreground" />
    )
  }

  return (
    <div className="flex w-full flex-col gap-4">
      <SystemCapacity
        concurrency={concurrencyQuery.data}
        limitedPolicies={limitedPolicies}
        policiesLoading={policiesQuery.isLoading}
      />

      <LatestRuns runs={runsQuery.data?.items ?? []} lagByRun={lagByRun} />
    </div>
  )
}

function SystemCapacity({
  concurrency,
  limitedPolicies,
  policiesLoading,
}: {
  concurrency?: CrawlConcurrencyLimits
  limitedPolicies: CrawlPolicyRecord[]
  policiesLoading: boolean
}) {
  const fetches = concurrency?.runtime_active ?? 0
  const capacity = concurrency?.runtime_capacity ?? 0
  const http = concurrency?.transports.find((item) => item.transport === "http")
  const browser = concurrency?.transports.find(
    (item) => item.transport === "browser",
  )

  return (
    <Card>
      <CardHeader>
        <CardTitle>System capacity limits</CardTitle>
        <p className="text-xs text-muted-foreground">
          Live page-fetch capacity and deployment-wide crawl policy limits.
        </p>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Limit
            label="Active page fetches"
            value={concurrency ? `${fetches} / ${capacity}` : "—"}
            detail="Pages currently held by crawl workers"
          />
          <Limit
            label="HTTP capacity"
            value={http ? `${http.active} / ${http.capacity}` : "—"}
            detail={http ? `${http.worker_count} workers online` : "Direct fetch pool"}
          />
          <Limit
            label="Browser capacity"
            value={browser ? `${browser.active} / ${browser.capacity}` : "—"}
            detail={browser ? `${browser.worker_count} workers online` : "Browser pool"}
          />
          <Limit
            label="Fetch wait timeout"
            value={
              concurrency ? `${concurrency.crawl_permit_timeout_seconds}s` : "—"
            }
            detail="Maximum wait for policy capacity"
          />
        </div>

        <div>
          <div className="mb-2 flex items-center justify-between gap-3">
            <p className="text-sm font-medium">Policy-specific limits</p>
            <span className="text-xs text-muted-foreground">
              Deployment-wide
            </span>
          </div>
          <div className="space-y-2">
            {limitedPolicies.map((policy) => (
              <a
                key={policy.id}
                href={`/crawl-policies/${policy.id}`}
                className="grid gap-1 rounded-md border p-3 text-sm transition-colors hover:bg-muted/50 sm:grid-cols-[minmax(10rem,1fr)_minmax(14rem,2fr)_auto] sm:items-center"
              >
                <span className="truncate font-medium">
                  {policy.domain_group}
                </span>
                <span className="truncate font-mono text-xs text-muted-foreground">
                  {policy.match}
                </span>
                <span className="tabular-nums">
                  {policy.concurrency} concurrent
                </span>
              </a>
            ))}
            {!policiesLoading && limitedPolicies.length === 0 ? (
              <p className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">
                No enabled CrawlPolicy has a concurrency limit.
              </p>
            ) : null}
          </div>
        </div>
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
        <p className="text-xs text-muted-foreground">
          Crawl progress and accumulated materialization lag for individual
          graph runs.
        </p>
      </CardHeader>
      <CardContent>
        <div className="overflow-hidden rounded-md border">
          <Table className="min-w-[52rem]">
            <TableHeader className="bg-muted/30">
              <TableRow className="hover:bg-transparent">
                <TableHead className="w-[9rem] pl-4">Status</TableHead>
                <TableHead className="w-[16rem]">Graph</TableHead>
                <TableHead>Crawl progress</TableHead>
                <TableHead className="w-[17rem] pr-4">
                  Materialization lag
                </TableHead>
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
              No graph runs yet.
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
  const failed =
    run.status === "failed" || run.status === "completed_with_errors"

  return (
    <TableRow title={`Run ${run.id}`}>
      <TableCell className="py-4 pl-4 align-top whitespace-normal">
        <Badge variant={failed || progress.stalled ? "destructive" : "outline"}>
          {progress.stalled
            ? "Stalled"
            : run.status === "completed_with_errors"
              ? "Needs attention"
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
            {progress.settled.toLocaleString()} /{" "}
            {run.request_count.toLocaleString()} settled
          </span>
          <span className="text-xs text-muted-foreground tabular-nums">
            {progress.activityLabel}
          </span>
        </div>
        <RunProgress run={run} className="mt-2" />
        {isActiveRun(run) && run.pending_request_count > 0 ? (
          <p className="mt-1 text-xs text-muted-foreground tabular-nums">
            {run.pending_request_count.toLocaleString()} pages remain
          </p>
        ) : null}
      </TableCell>
      <TableCell className="py-4 pr-4 align-top whitespace-normal">
        <MaterializationLag lag={lag} />
      </TableCell>
    </TableRow>
  )
}

function MaterializationLag({ lag }: { lag?: GraphRunMaterializationLag }) {
  if (!lag || lag.materialization_count === 0) {
    return (
      <div>
        <p className="font-medium">None</p>
        <p className="mt-1 text-xs text-muted-foreground">
          No affected materialized views
        </p>
      </div>
    )
  }

  const unresolved = lag.pending_updates + lag.failed_updates
  const views = `${lag.materialization_count.toLocaleString()} ${lag.materialization_count === 1 ? "view" : "views"}`

  if (unresolved === 0) {
    return (
      <div>
        <p className="font-medium">None</p>
        <p className="mt-1 text-xs text-muted-foreground">
          Settled across {views}
        </p>
      </div>
    )
  }

  return (
    <div>
      <p
        className={`font-medium tabular-nums ${lag.failed_updates ? "text-destructive" : ""}`}
      >
        {unresolved.toLocaleString()} unresolved
      </p>
      <p className="mt-1 text-xs text-muted-foreground tabular-nums">
        {lag.pending_updates.toLocaleString()} pending ·{" "}
        {lag.failed_updates.toLocaleString()} failed · {views}
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
        className="h-full rounded-full bg-primary transition-[width]"
        style={{ width: `${percent}%` }}
      />
    </div>
  )
}

function isActiveRun(run: GraphRunRecord) {
  return run.status === "queued" || run.status === "running"
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
  const label = value.replaceAll("_", " ")
  return `${label.charAt(0).toUpperCase()}${label.slice(1)}`
}

function Limit({
  label,
  value,
  detail,
}: {
  label: string
  value: ReactNode
  detail: string
}) {
  return (
    <div className="rounded-md border p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-xl font-semibold tabular-nums">{value}</p>
      <p className="mt-1 text-xs text-muted-foreground">{detail}</p>
    </div>
  )
}
