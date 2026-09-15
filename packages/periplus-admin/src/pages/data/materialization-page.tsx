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
  const candidate = runs.data?.some(run => ["preparing", "building", "verifying", "ready", "cancelling", "draining"].includes(run.phase))
  return <div className="mx-auto flex max-w-7xl flex-col gap-6">
    <div className="flex flex-wrap items-center justify-between gap-4"><h1 className="text-2xl font-semibold">Materialization rebuilds</h1>
      <div className="flex items-center gap-2"><label className="whitespace-nowrap" htmlFor="page-size">Events per batch</label><Input className="w-24" id="page-size" type="number" min={1} max={128} value={pageSize} onChange={event => setPageSize(Number(event.target.value))} /><Button disabled={!runs.data || candidate || mutation.isPending || !Number.isInteger(pageSize) || pageSize < 1 || pageSize > 128} onClick={() => mutation.mutate({ page_size: pageSize })}>Start rebuild</Button></div></div>
    <p>Live queries keep using the serving target while a candidate rebuilds history and catches up. Activation is explicit.</p>
    {runs.error && <p role="alert">{extractApiError(runs.error)}</p>}
    {runs.isPending && <p>Loading rebuilds…</p>}
    {runs.data?.map(run => {
      const actions: BuildAction[] = []
      if (run.phase === "building") actions.push(run.paused ? "resume" : "pause")
      if (run.blocker) actions.push("retry")
      if (["preparing", "building", "verifying", "ready"].includes(run.phase)) actions.push("cancel")
      if (run.phase === "ready" && !run.blocker && run.verified_at !== null) actions.push("activate")
      return <Card key={run.id}><CardHeader><CardTitle className="flex flex-wrap items-center gap-3">
        <Badge>{run.phase}</Badge>{run.paused && <Badge variant="secondary">History paused</Badge>}{run.id}
        </CardTitle><CardDescription>Created {new Date(run.created_at).toLocaleString()} · {run.recipe.slice(0, 12)}</CardDescription></CardHeader>
        <CardContent className="flex flex-col gap-4">
          <p>{run.ranges.filter(r => r.cursor === r.upper).length} / {run.ranges.length} historical ranges complete · {run.ranges.reduce((n, r) => n + r.processed, 0).toLocaleString()} capture events verified</p>
          <p>Coverage verified: {run.verified_at ? new Date(run.verified_at).toLocaleTimeString() : "In progress"} · Manifest: {run.manifest_key ?? "Preparing"}</p>
          {run.blocker && <p role="alert">Blocked: {run.blocker}. Inspect the worker logs and input, repair the cause, then retry.</p>}
          {run.phase === "cancelling" && <p>Waiting for bounded writers to drain. Source protection remains held until {run.drain_after && new Date(run.drain_after).toLocaleTimeString()}.</p>}
          <details><summary>Target and scan details</summary><p>Material: {run.material_database} · Query views: {run.query_database}</p>
            <Table><TableHeader><TableRow><TableHead>Shard</TableHead><TableHead>Verified captures</TableHead><TableHead>History / initial upper</TableHead><TableHead>Status</TableHead></TableRow></TableHeader><TableBody>
              {run.ranges.map(range => <TableRow key={range.shard}><TableCell>{range.shard}</TableCell><TableCell>{range.processed}</TableCell><TableCell>{range.cursor} / {range.upper}</TableCell><TableCell>Live through {range.live_cursor}</TableCell></TableRow>)}
            </TableBody></Table></details>
          {run.batches.length > 0 && <Table><TableHeader><TableRow><TableHead>Batch</TableHead><TableHead>Work</TableHead><TableHead>Worker</TableHead><TableHead>Status</TableHead></TableRow></TableHeader><TableBody>
            {run.batches.map(batch => <TableRow key={batch.id}><TableCell>{batch.id}</TableCell><TableCell>{batch.lane} · shard {batch.shard} · {batch.start}–{batch.end}</TableCell><TableCell>{batch.worker_id ?? "Unassigned"}</TableCell><TableCell>{batch.status} · {batch.attempts} attempts{batch.error && <p role="alert">{batch.error}</p>}</TableCell></TableRow>)}
          </TableBody></Table>}
          <div className="flex gap-2">{actions.map(action => <Button key={action} variant={action === "activate" ? "default" : "outline"} disabled={mutation.isPending} onClick={() => mutation.mutate({ id: run.id, action })}>{action[0].toUpperCase() + action.slice(1)}</Button>)}</div>
        </CardContent></Card>
    })}
  </div>
}
