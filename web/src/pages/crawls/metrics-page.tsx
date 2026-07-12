import { ActivityIcon, CircleCheckIcon, CircleXIcon, LoaderCircleIcon } from "lucide-react"
import type { ReactNode } from "react"

import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { useCrawlConcurrencyLimits, useGraphRuns } from "@/hooks/use-crawl-graphs"
import { useCrawlPolicies } from "@/hooks/use-resource-data"

const policyFilters = {
  matchPattern: "",
  enabled: "enabled" as const,
  template: "",
  mode: "all" as const,
}

export function CrawlMetricsPage() {
  const runsQuery = useGraphRuns()
  const concurrencyQuery = useCrawlConcurrencyLimits()
  const policiesQuery = useCrawlPolicies(policyFilters, { limit: 500, offset: 0 })
  const runs = runsQuery.data?.items ?? []
  const active = runs.filter((run) => run.status === "queued" || run.status === "running").length
  const completed = runs.filter((run) => run.status === "completed").length
  const failed = runs.filter((run) => run.status === "failed" || run.status === "completed_with_errors").length
  const requests = runs.reduce((total, run) => total + run.request_count, 0)
  const concurrency = concurrencyQuery.data
  const limitedPolicies = (policiesQuery.data?.items ?? []).filter(
    (policy) => policy.max_concurrency !== null
  )

  if (runsQuery.isLoading) {
    return <LoaderCircleIcon className="m-auto size-5 animate-spin text-muted-foreground" />
  }

  return (
    <div className="flex w-full flex-col gap-4">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <Metric title="Graph runs" value={runsQuery.data?.total ?? 0} icon={<ActivityIcon />} />
        <Metric title="Active" value={active} icon={<LoaderCircleIcon />} />
        <Metric title="Completed" value={completed} icon={<CircleCheckIcon />} />
        <Metric title="Failed" value={failed} icon={<CircleXIcon className={failed ? "text-destructive" : undefined} />} />
        <Metric title="Requests admitted" value={requests} icon={<ActivityIcon />} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Concurrency limits</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Limit
              label="Runtime work slots"
              value={concurrency ? `${concurrency.runtime_active} / ${concurrency.runtime_capacity}` : "—"}
              detail="Active work across live graph workers"
            />
            <Limit
              label="Browser crawl slots"
              value={concurrency?.browser_capacity ?? "—"}
              detail={concurrency ? `${concurrency.browser_concurrency_per_worker} per worker` : "Deployment limit"}
            />
            <Limit
              label="Live workers"
              value={concurrency?.worker_count ?? "—"}
              detail="Worker presence reported through NATS"
            />
            <Limit
              label="Permit wait timeout"
              value={concurrency ? `${concurrency.crawl_permit_timeout_seconds}s` : "—"}
              detail="Maximum wait for a crawl slot"
            />
          </div>

          <div>
            <div className="mb-2 flex items-center justify-between gap-3">
              <p className="text-sm font-medium">Policy-specific limits</p>
              <span className="text-xs text-muted-foreground">Per worker</span>
            </div>
            <div className="space-y-2">
              {limitedPolicies.map((policy) => (
                <a
                  key={policy.id}
                  href={`/settings/crawl-policies/${policy.id}`}
                  className="grid gap-1 rounded-md border p-3 text-sm transition-colors hover:bg-muted/50 sm:grid-cols-[minmax(10rem,1fr)_minmax(14rem,2fr)_auto] sm:items-center"
                >
                  <span className="truncate font-medium">{policy.domain_group}</span>
                  <span className="truncate font-mono text-xs text-muted-foreground">{policy.match}</span>
                  <span className="tabular-nums">{policy.max_concurrency} concurrent</span>
                </a>
              ))}
              {!policiesQuery.isLoading && limitedPolicies.length === 0 ? (
                <p className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">
                  No enabled CrawlPolicy has an additional concurrency limit.
                </p>
              ) : null}
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Recent graph runs</CardTitle></CardHeader>
        <CardContent className="space-y-2">
          {runs.map((run) => {
            const estimate = estimateRun(run)
            return (
            <div key={run.id} className="grid gap-2 rounded-md border p-3 text-sm md:grid-cols-[minmax(16rem,1fr)_auto_auto_auto_auto] md:items-center">
              <div className="min-w-0">
                <p className="truncate font-medium">{run.graph_name ?? "Deleted graph"}</p>
                <p className="truncate font-mono text-[0.6875rem] text-muted-foreground">Graph {run.graph_id}</p>
                <p className="truncate font-mono text-[0.6875rem] text-muted-foreground">Run {run.id}</p>
                <p className="mt-1 text-xs text-muted-foreground">{new Date(run.created_at).toLocaleString()}</p>
              </div>
              <Badge variant={run.status === "failed" ? "destructive" : "outline"}>{run.status}</Badge>
              <span className="tabular-nums">{estimate.settled} / {run.request_count} settled</span>
              <span className="tabular-nums text-muted-foreground">{run.pending_request_count} known remaining</span>
              <span className="tabular-nums text-muted-foreground">{estimate.throughputLabel ?? "— requests/min"}</span>
            </div>
          )})}
          {runs.length === 0 ? <p className="py-8 text-center text-sm text-muted-foreground">No graph runs yet.</p> : null}
        </CardContent>
      </Card>
    </div>
  )
}

function estimateRun(run: {
  request_count: number
  pending_request_count: number
  started_at: string | null
  completed_at: string | null
}) {
  const settled = Math.max(0, run.request_count - run.pending_request_count)
  if (!run.started_at || settled === 0) {
    return { settled, throughputLabel: null }
  }
  const end = run.completed_at ? new Date(run.completed_at).getTime() : Date.now()
  const elapsedSeconds = Math.max(1, (end - new Date(run.started_at).getTime()) / 1_000)
  const requestsPerMinute = settled / elapsedSeconds * 60
  return {
    settled,
    throughputLabel: `${requestsPerMinute.toFixed(requestsPerMinute < 10 ? 1 : 0)} requests/min`,
  }
}

function Limit({ label, value, detail }: { label: string; value: ReactNode; detail: string }) {
  return (
    <div className="rounded-md border p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-xl font-semibold tabular-nums">{value}</p>
      <p className="mt-1 text-xs text-muted-foreground">{detail}</p>
    </div>
  )
}

function Metric({ title, value, icon }: { title: string; value: number; icon: ReactNode }) {
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        <span className="text-muted-foreground [&>svg]:size-4">{icon}</span>
      </CardHeader>
      <CardContent><p className="text-2xl font-semibold tabular-nums">{value}</p></CardContent>
    </Card>
  )
}
