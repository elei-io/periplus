import { ActivityIcon, CircleCheckIcon, CircleXIcon, LoaderCircleIcon } from "lucide-react"
import type { ReactNode } from "react"

import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { useGraphRuns } from "@/hooks/use-crawl-graphs"

export function CrawlMetricsPage() {
  const runsQuery = useGraphRuns()
  const runs = runsQuery.data?.items ?? []
  const selectedRunId = new URLSearchParams(window.location.search).get("run")
  const selectedRun = runs.find((run) => run.id === selectedRunId)
  const active = runs.filter((run) => run.status === "queued" || run.status === "running").length
  const completed = runs.filter((run) => run.status === "completed").length
  const failed = runs.filter((run) => run.status === "failed" || run.status === "completed_with_errors").length
  const requests = runs.reduce((total, run) => total + run.request_count, 0)

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

      {selectedRun ? (
        <Card className="border-primary/30">
          <CardHeader><CardTitle>Run {selectedRun.id}</CardTitle></CardHeader>
          <CardContent className="grid gap-3 text-sm sm:grid-cols-2 xl:grid-cols-4">
            <span>Status: <Badge variant="outline">{selectedRun.status}</Badge></span>
            <span>{selectedRun.request_count} requests admitted</span>
            <span>{selectedRun.pending_request_count} pending</span>
            <span>{selectedRun.failed_request_count} failed</span>
            <span className="sm:col-span-2 xl:col-span-4 text-muted-foreground">
              Started {selectedRun.started_at ? new Date(selectedRun.started_at).toLocaleString() : "not yet"}
              {selectedRun.completed_at ? ` · Completed ${new Date(selectedRun.completed_at).toLocaleString()}` : ""}
            </span>
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader><CardTitle>Recent graph runs</CardTitle></CardHeader>
        <CardContent className="space-y-2">
          {runs.map((run) => (
            <div key={run.id} className={`grid gap-2 rounded-md border p-3 text-sm md:grid-cols-[minmax(12rem,1fr)_auto_auto_auto] md:items-center ${run.id === selectedRunId ? "border-primary/40 bg-primary/5" : ""}`}>
              <div className="min-w-0">
                <p className="truncate font-mono text-xs">{run.id}</p>
                <p className="text-xs text-muted-foreground">{new Date(run.created_at).toLocaleString()}</p>
              </div>
              <Badge variant={run.status === "failed" ? "destructive" : "outline"}>{run.status}</Badge>
              <span className="tabular-nums">{run.request_count} requests</span>
              <span className="tabular-nums text-muted-foreground">{run.failed_request_count} failed</span>
            </div>
          ))}
          {runs.length === 0 ? <p className="py-8 text-center text-sm text-muted-foreground">No graph runs yet.</p> : null}
        </CardContent>
      </Card>
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
