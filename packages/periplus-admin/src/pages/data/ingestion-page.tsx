import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { useIngestion } from "@/hooks/use-ingestion"
import { extractApiError } from "@/lib/api"

export function IngestionPage() {
  const query = useIngestion()
  const report = query.data
  return <div className="flex flex-col gap-5">
    <div className="flex items-center justify-between"><h1 className="text-2xl font-semibold">Archive and delivery</h1>
      <Button variant="outline" disabled={query.isFetching} onClick={() => void query.refetch()}>Refresh</Button></div>
    <p>Captures become durable in the raw archive. Workers build the queryable corpus from that archive.</p>
    {query.error && <p role="alert">{extractApiError(query.error)}</p>}
    {query.isPending && <p>Loading archive coverage…</p>}
    {report && <>
      <Badge variant={report.status === "attention" ? "destructive" : "outline"}>{report.status}</Badge>
      <Card><CardHeader><CardTitle>{report.archive_events.toLocaleString()} archive events</CardTitle>
        <CardDescription>Events include captures, retirements and possible duplicate notifications.</CardDescription></CardHeader>
        <CardContent><p>{report.queue.pending} batches awaiting delivery · {report.queue.ack_pending} awaiting acknowledgement · {report.queue.redelivered} redelivered</p>
          <p>An empty queue does not prove completeness. The archive checkpoints below do.</p></CardContent></Card>
      <Card><CardHeader><CardTitle>Query targets</CardTitle><CardDescription>Lag counts archive events still to verify.</CardDescription></CardHeader>
        <CardContent><Table><TableHeader><TableRow><TableHead>Target</TableHead><TableHead>Phase</TableHead><TableHead>Lag</TableHead><TableHead>Running</TableHead><TableHead>Failures</TableHead></TableRow></TableHeader>
          <TableBody>{report.targets.map(target => <TableRow key={target.id}><TableCell>{target.id}</TableCell><TableCell>{target.phase}</TableCell><TableCell>{target.source_lag ?? "Preparing"}</TableCell><TableCell>{target.running_batches}</TableCell><TableCell>{target.failed_batches}{target.blocker && <p role="alert">{target.blocker}</p>}</TableCell></TableRow>)}</TableBody></Table>
          <a href="/data/materialization" className="underline">Inspect batches and retry in Materialization</a></CardContent></Card>
      <p>Observed {new Date(report.generated_at).toLocaleString()}. Refreshes every 10 seconds.</p>
    </>}
  </div>
}
