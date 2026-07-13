import {
  ActivityIcon,
  ChevronDownIcon,
  CircleCheckIcon,
  ExternalLinkIcon,
  LoaderCircleIcon,
  TriangleAlertIcon,
} from "lucide-react"
import { useState, type ReactNode } from "react"

import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  useCrawlConcurrencyLimits,
  useCrawlGraphs,
  useGraphRunMaterializationLag,
  useGraphRuns,
} from "@/hooks/use-crawl-graphs"
import { useCrawlPolicies } from "@/hooks/use-resource-data"
import type {
  CrawlConcurrencyLimits,
  CrawlGraphSummary,
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
  const graphsQuery = useCrawlGraphs()
  const runsQuery = useGraphRuns()
  const concurrencyQuery = useCrawlConcurrencyLimits()
  const materializationLagQuery = useGraphRunMaterializationLag()
  const policiesQuery = useCrawlPolicies(policyFilters, {
    limit: 500,
    offset: 0,
  })
  const runs = runsQuery.data?.items ?? []
  const activeRuns = runs.filter(isActiveRun)
  const activeGraphCount = new Set(activeRuns.map((run) => run.graph_id)).size
  const requestsRemaining = activeRuns.reduce(
    (total, run) => total + run.pending_request_count,
    0
  )
  const stalledRuns = activeRuns.filter(
    (run) => describeRunProgress(run).stalled
  ).length
  const lagByRun = new Map(
    (materializationLagQuery.data?.items ?? []).map((lag) => [lag.run_id, lag])
  )
  const runsByGraph = runs.reduce((grouped, run) => {
    const graphRuns = grouped.get(run.graph_id) ?? []
    graphRuns.push(run)
    grouped.set(run.graph_id, graphRuns)
    return grouped
  }, new Map<string, GraphRunRecord[]>())
  const currentLags = [...runsByGraph.values()]
    .map((graphRuns) => lagByRun.get(graphRuns[0]?.id ?? ""))
    .filter((lag): lag is GraphRunMaterializationLag => lag !== undefined)
  const pendingMaterializations = currentLags.reduce(
    (total, lag) => total + lag.pending_updates,
    0
  )
  const failedMaterializations = currentLags.reduce(
    (total, lag) => total + lag.failed_updates,
    0
  )
  const limitedPolicies = (policiesQuery.data?.items ?? []).filter(
    (policy) => policy.concurrency !== null
  )

  if (
    graphsQuery.isLoading ||
    runsQuery.isLoading ||
    materializationLagQuery.isLoading
  ) {
    return (
      <LoaderCircleIcon className="m-auto size-5 animate-spin text-muted-foreground" />
    )
  }

  return (
    <div className="flex w-full flex-col gap-4">
      <ActivitySummary
        activeGraphCount={activeGraphCount}
        requestsRemaining={requestsRemaining}
        pendingMaterializations={pendingMaterializations}
        failedMaterializations={failedMaterializations}
        stalledRuns={stalledRuns}
      />

      <Card>
        <CardHeader>
          <CardTitle>Graphs</CardTitle>
          <p className="text-xs text-muted-foreground">
            Crawl progress and materialization health for every graph.
          </p>
        </CardHeader>
        <CardContent>
          <div className="overflow-hidden rounded-md border">
            {(graphsQuery.data?.items ?? []).map((graph) => (
              <GraphRow
                key={graph.id}
                graph={graph}
                runs={(runsByGraph.get(graph.id) ?? []).slice(0, 3)}
                lagByRun={lagByRun}
              />
            ))}
            {graphsQuery.data?.items.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted-foreground">
                No graphs yet.
              </p>
            ) : null}
          </div>
        </CardContent>
      </Card>

      <SystemCapacity
        concurrency={concurrencyQuery.data}
        limitedPolicies={limitedPolicies}
        policiesLoading={policiesQuery.isLoading}
      />
    </div>
  )
}

function ActivitySummary({
  activeGraphCount,
  requestsRemaining,
  pendingMaterializations,
  failedMaterializations,
  stalledRuns,
}: {
  activeGraphCount: number
  requestsRemaining: number
  pendingMaterializations: number
  failedMaterializations: number
  stalledRuns: number
}) {
  const needsAttention = stalledRuns > 0 || failedMaterializations > 0
  const title = needsAttention
    ? "Crawl activity needs attention"
    : activeGraphCount > 0
      ? `${activeGraphCount} ${activeGraphCount === 1 ? "graph is" : "graphs are"} running`
      : pendingMaterializations > 0
        ? "Crawling is finished; materialization is catching up"
        : "All graphs are settled"
  const details: string[] = []

  if (stalledRuns > 0) {
    details.push(
      `${stalledRuns} active ${stalledRuns === 1 ? "run has" : "runs have"} stopped making progress`
    )
  } else if (activeGraphCount > 0) {
    details.push(
      `${requestsRemaining.toLocaleString()} ${requestsRemaining === 1 ? "page remains" : "pages remain"}`
    )
  } else {
    details.push("No crawl work is running")
  }
  if (failedMaterializations > 0) {
    details.push(
      `Materialization has ${failedMaterializations.toLocaleString()} ${failedMaterializations === 1 ? "update that needs" : "updates that need"} attention`
    )
  } else if (pendingMaterializations > 0) {
    details.push(
      `Materialization has ${pendingMaterializations.toLocaleString()} ${pendingMaterializations === 1 ? "update" : "updates"} still settling`
    )
  } else {
    details.push("Materialization is up to date")
  }

  const Icon = needsAttention
    ? TriangleAlertIcon
    : activeGraphCount > 0 || pendingMaterializations > 0
      ? ActivityIcon
      : CircleCheckIcon

  return (
    <Card size="sm">
      <CardContent className="flex items-start gap-3">
        <span
          className={`mt-0.5 rounded-full p-2 ${needsAttention ? "bg-destructive/10 text-destructive" : "bg-primary/10 text-primary"}`}
        >
          <Icon className="size-4" />
        </span>
        <div className="min-w-0">
          <p className="text-sm font-medium">{title}</p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {details.join(". ")}.
          </p>
        </div>
      </CardContent>
    </Card>
  )
}

function GraphRow({
  graph,
  runs,
  lagByRun,
}: {
  graph: CrawlGraphSummary
  runs: GraphRunRecord[]
  lagByRun: Map<string, GraphRunMaterializationLag>
}) {
  const [open, setOpen] = useState(false)
  const currentRun = runs.find(isActiveRun) ?? runs[0]

  return (
    <div className="border-b last:border-b-0">
      <div className="grid gap-4 p-4 lg:grid-cols-2 lg:items-start 2xl:grid-cols-[minmax(13rem,0.85fr)_minmax(17rem,1.2fr)_minmax(16rem,1fr)_auto] 2xl:items-center">
        <button
          type="button"
          aria-expanded={open}
          aria-label={`${open ? "Hide" : "Show"} recent runs for ${graph.name}`}
          className="group grid min-w-0 grid-cols-[auto_minmax(0,1fr)] items-center gap-3 rounded-sm text-left transition-colors hover:text-foreground lg:col-span-2 2xl:col-span-1"
          onClick={() => setOpen((value) => !value)}
        >
          <span className="rounded-sm p-1 text-muted-foreground transition-colors group-hover:bg-muted">
            <ChevronDownIcon
              className={`size-4 transition-transform ${open ? "rotate-0" : "-rotate-90"}`}
            />
          </span>
          <span className="min-w-0">
            <span className="block truncate text-sm font-medium">
              {graph.name}
            </span>
            <span className="block truncate text-xs text-muted-foreground">
              {graph.description || "No description"}
            </span>
          </span>
        </button>
        <CrawlSummary run={currentRun} />
        <MaterializationSummary
          lag={currentRun ? lagByRun.get(currentRun.id) : undefined}
        />
        <a
          href={`/crawls/graphs/${graph.id}`}
          className="ml-8 inline-flex items-center gap-1 text-xs font-medium hover:underline lg:col-span-2 lg:ml-0 2xl:col-span-1 2xl:justify-self-end"
        >
          View graph <ExternalLinkIcon className="size-3" />
        </a>
      </div>

      {open ? (
        <div className="border-t bg-muted/20 px-4 py-3 md:pl-12">
          <p className="mb-2 text-[0.6875rem] font-medium tracking-wide text-muted-foreground uppercase">
            Latest runs
          </p>
          {runs.length ? (
            <div className="space-y-2">
              {runs.map((run) => (
                <RunRow key={run.id} run={run} lag={lagByRun.get(run.id)} />
              ))}
            </div>
          ) : (
            <p className="py-3 text-sm text-muted-foreground">
              This graph has not run yet.
            </p>
          )}
        </div>
      ) : null}
    </div>
  )
}

function CrawlSummary({ run }: { run?: GraphRunRecord }) {
  if (!run) {
    return (
      <div>
        <SummaryLabel>Crawl activity</SummaryLabel>
        <p className="mt-1 text-sm font-medium">Not run yet</p>
      </div>
    )
  }
  const progress = describeRunProgress(run)
  const active = isActiveRun(run)
  const failed =
    run.status === "failed" || run.status === "completed_with_errors"

  return (
    <div className="min-w-0">
      <SummaryLabel>{active ? "Current crawl" : "Latest crawl"}</SummaryLabel>
      <div className="mt-1 flex min-w-0 items-center gap-2">
        <Badge
          variant={failed || progress.stalled ? "destructive" : "outline"}
        >
          {progress.stalled
            ? "Stalled"
            : run.status === "completed_with_errors"
              ? "Needs attention"
              : sentenceCase(run.status)}
        </Badge>
        <span className="truncate text-sm font-medium tabular-nums">
          {active
            ? `${progress.settled.toLocaleString()} of ${run.request_count.toLocaleString()} pages`
            : `${run.request_count.toLocaleString()} ${run.request_count === 1 ? "page" : "pages"}`}
        </span>
      </div>
      {active ? <RunProgress run={run} className="mt-2" /> : null}
      <p className="mt-1 truncate text-xs text-muted-foreground tabular-nums">
        {active
          ? `${run.pending_request_count.toLocaleString()} remaining · ${progress.activityLabel}`
          : progress.activityLabel}
      </p>
    </div>
  )
}

function MaterializationSummary({ lag }: { lag?: GraphRunMaterializationLag }) {
  if (!lag || lag.materialization_count === 0) {
    return (
      <div className="min-w-0">
        <SummaryLabel>Materialization</SummaryLabel>
        <p className="mt-1 text-sm font-medium">No view updates</p>
        <p className="mt-1 text-xs text-muted-foreground">
          This run did not affect a materialized view.
        </p>
      </div>
    )
  }
  const views = `${lag.materialization_count.toLocaleString()} ${lag.materialization_count === 1 ? "view" : "views"}`
  if (lag.failed_updates > 0) {
    return (
      <div className="min-w-0">
        <SummaryLabel>Materialization</SummaryLabel>
        <p className="mt-1 text-sm font-medium text-destructive">
          Needs attention
        </p>
        <p className="mt-1 truncate text-xs text-muted-foreground tabular-nums">
          {lag.failed_updates.toLocaleString()} failed ·{" "}
          {lag.pending_updates.toLocaleString()} pending across {views}
        </p>
      </div>
    )
  }
  if (lag.pending_updates > 0) {
    return (
      <div className="min-w-0">
        <SummaryLabel>Materialization</SummaryLabel>
        <p className="mt-1 text-sm font-medium">Catching up</p>
        <p className="mt-1 truncate text-xs text-muted-foreground tabular-nums">
          {lag.pending_updates.toLocaleString()} {" "}
          {lag.pending_updates === 1 ? "update" : "updates"} pending across{" "}
          {views}
        </p>
      </div>
    )
  }
  return (
    <div className="min-w-0">
      <SummaryLabel>Materialization</SummaryLabel>
      <p className="mt-1 text-sm font-medium">Up to date</p>
      <p className="mt-1 text-xs text-muted-foreground">Settled across {views}</p>
    </div>
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
    <div
      className="grid gap-2 rounded-md border bg-background/60 p-3 text-sm sm:grid-cols-[minmax(10rem,0.8fr)_minmax(14rem,1.4fr)_auto] sm:items-center"
      title={`Run ${run.id}`}
    >
      <div className="min-w-0">
        <p className="font-medium">
          {run.started_at
            ? `Started ${relativeTime(run.started_at)}`
            : `Queued ${relativeTime(run.created_at)}`}
        </p>
        <p className="text-xs text-muted-foreground">
          {new Date(run.created_at).toLocaleString()}
        </p>
      </div>
      <div className="min-w-0">
        <div className="flex items-center justify-between gap-3">
          <span className="font-medium tabular-nums">
            {progress.settled.toLocaleString()} of{" "}
            {run.request_count.toLocaleString()} pages
          </span>
          <span className="text-xs text-muted-foreground tabular-nums">
            {progress.activityLabel}
          </span>
        </div>
        <RunProgress run={run} className="mt-2" />
        {lag && lag.pending_updates + lag.failed_updates > 0 ? (
          <p className="mt-1 text-xs text-muted-foreground tabular-nums">
            Materialization: {lag.pending_updates.toLocaleString()} pending ·{" "}
            {lag.failed_updates.toLocaleString()} failed
          </p>
        ) : null}
      </div>
      <Badge variant={failed || progress.stalled ? "destructive" : "outline"}>
        {progress.stalled
          ? "Stalled"
          : run.status === "completed_with_errors"
            ? "Needs attention"
            : sentenceCase(run.status)}
      </Badge>
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

function SystemCapacity({
  concurrency,
  limitedPolicies,
  policiesLoading,
}: {
  concurrency?: CrawlConcurrencyLimits
  limitedPolicies: CrawlPolicyRecord[]
  policiesLoading: boolean
}) {
  const [open, setOpen] = useState(false)
  const fetches = concurrency?.runtime_active ?? 0
  const capacity = concurrency?.runtime_capacity ?? 0
  const workers = concurrency?.worker_count ?? 0

  return (
    <Card size="sm">
      <button
        type="button"
        aria-expanded={open}
        className="flex items-center justify-between gap-4 px-3 text-left"
        onClick={() => setOpen((value) => !value)}
      >
        <div>
          <p className="text-sm font-medium">System capacity</p>
          <p className="mt-0.5 text-xs text-muted-foreground tabular-nums">
            {fetches} {fetches === 1 ? "page fetch" : "page fetches"} active ·{" "}
            {capacity} maximum · {workers} {workers === 1 ? "worker" : "workers"} online
          </p>
        </div>
        <ChevronDownIcon
          className={`size-4 shrink-0 text-muted-foreground transition-transform ${open ? "rotate-0" : "-rotate-90"}`}
        />
      </button>
      {open ? (
        <CardContent className="space-y-4 border-t pt-3">
          <p className="text-xs text-muted-foreground">
            Page-fetch capacity is instantaneous. A graph can remain active while
            fetched pages are being stored, materialized, or used to discover the
            next pages.
          </p>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Limit
              label="Active page fetches"
              value={concurrency ? `${fetches} / ${capacity}` : "—"}
              detail="Pages currently held by crawl workers"
            />
            <Limit
              label="Browser capacity"
              value={concurrency?.browser_capacity ?? "—"}
              detail={
                concurrency
                  ? `${concurrency.browser_concurrency_per_worker} per worker`
                  : "Deployment limit"
              }
            />
            <Limit
              label="Crawl workers"
              value={concurrency?.worker_count ?? "—"}
              detail="Workers reporting as online"
            />
            <Limit
              label="Fetch wait timeout"
              value={
                concurrency
                  ? `${concurrency.crawl_permit_timeout_seconds}s`
                  : "—"
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
                  href={`/settings/crawl-policies/${policy.id}`}
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
      ) : null}
    </Card>
  )
}

function SummaryLabel({ children }: { children: ReactNode }) {
  return (
    <p className="text-[0.6875rem] font-medium tracking-wide text-muted-foreground uppercase">
      {children}
    </p>
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
