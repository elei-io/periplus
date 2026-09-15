import { useState } from "react"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { useBuildAction, useMaterializationRuns } from "@/hooks/use-materialization"
import { extractApiError } from "@/lib/api"
import type { BuildAction } from "@/types/materialization"

export function MaterializationPage() {
  const [pageSize, setPageSize] = useState(32)
  const runs = useMaterializationRuns()
  const mutation = useBuildAction()
  const candidate = runs.data?.some(run => ["preparing", "building", "verifying", "ready", "cancelling"].includes(run.phase))
  return <div className="mx-auto flex max-w-7xl flex-col gap-6">
    <div className="flex flex-wrap items-center justify-between gap-4"><h1 className="text-2xl font-semibold">Materialization rebuilds</h1>
      <div className="flex items-center gap-2"><label className="whitespace-nowrap" htmlFor="page-size">Visits per page</label><Input className="w-24" id="page-size" type="number" min={1} max={128} value={pageSize} onChange={event => setPageSize(Number(event.target.value))} /><Button disabled={!runs.data || candidate || mutation.isPending || !Number.isInteger(pageSize) || pageSize < 1 || pageSize > 128} onClick={() => mutation.mutate({ page_size: pageSize })}>Start rebuild</Button></div></div>
    <p>Live queries keep using the serving target while a candidate rebuilds history and catches up. Activation is explicit.</p>
    {runs.error && <p role="alert">{extractApiError(runs.error)}</p>}
    {runs.isPending && <p>Loading rebuilds…</p>}
    {runs.data?.map(run => {
      const actions: BuildAction[] = []
      if (run.phase === "building") actions.push(run.paused ? "resume" : "pause")
      if (run.blocker) actions.push("retry")
      if (["preparing", "building", "verifying", "ready"].includes(run.phase)) actions.push("cancel")
      if (run.phase === "ready" && !run.blocker && run.live_pending === 0) actions.push("activate")
      return <Card key={run.id}><CardHeader><CardTitle className="flex flex-wrap items-center gap-3">
        <Badge>{run.phase}</Badge>{run.paused && <Badge variant="secondary">History paused</Badge>}{run.id}
        </CardTitle><CardDescription>Created {new Date(run.created_at).toLocaleString()} · {run.semantic_version}</CardDescription></CardHeader>
        <CardContent className="flex flex-col gap-4">
          <p>{run.ranges.filter(r => r.done).length} / {run.ranges.length} historical ranges complete · {run.ranges.reduce((n, r) => n + r.processed, 0).toLocaleString()} visits verified · {run.live_pending} live deliveries pending</p>
          <p>Catch-up barrier: {run.barrier ?? "Not published"} · Ingestion ACK floor: {run.ingestion_floor} · Material ACK floor: {run.material_floor}</p>
          {run.blocker && <p role="alert">Blocked: {run.blocker}. Inspect the worker logs and input, repair the cause, then retry.</p>}
          {run.phase === "cancelling" && <p>Waiting for bounded writers to drain. Source protection remains held until {run.drain_after && new Date(run.drain_after).toLocaleTimeString()}.</p>}
          <details><summary>Target and scan details</summary><p>Material: {run.material_database} · Query views: {run.query_database} · Consumer: {run.consumer}</p>
            <Table><TableHeader><TableRow><TableHead>Month</TableHead><TableHead>Verified visits</TableHead><TableHead>Last durable key</TableHead><TableHead>Status</TableHead></TableRow></TableHeader><TableBody>
              {run.ranges.map(range => <TableRow key={range.month}><TableCell>{range.month}</TableCell><TableCell>{range.processed}</TableCell><TableCell>{range.cursor?.join(" · ") ?? "Not started"}</TableCell><TableCell>{range.done ? "Complete" : "Pending"}</TableCell></TableRow>)}
            </TableBody></Table></details>
          <div className="flex gap-2">{actions.map(action => <Button key={action} variant={action === "activate" ? "default" : "outline"} disabled={mutation.isPending} onClick={() => mutation.mutate({ id: run.id, action })}>{action[0].toUpperCase() + action.slice(1)}</Button>)}</div>
        </CardContent></Card>
    })}
  </div>
}
