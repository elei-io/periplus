import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { useStorage } from "@/hooks/use-storage"
import { extractApiError } from "@/lib/api"
import { bytes } from "./storage-format"
export function StoragePage() {
  const query = useStorage()
  const data = query.data
  return <div className="mx-auto flex max-w-7xl flex-col gap-6">
    <div className="flex items-center justify-between"><h1>Storage</h1><Button variant="outline" disabled={query.isFetching} onClick={() => void query.refetch()}>Refresh</Button></div>
    {query.error && <p role="alert">{extractApiError(query.error)}</p>}
    {!data && <p>Reading storage metadata…</p>}
    {data && <><p>Measured {new Date(data.collected_at).toLocaleString()}. Raw retention is disabled; rebuild targets and source bytes remain protected.</p>
      <div className="grid gap-4 md:grid-cols-2">{data.sources.map(source => <Card key={source.id}><CardHeader><CardTitle>{source.name}: {bytes(source.bytes)}</CardTitle><CardDescription>{source.basis}</CardDescription></CardHeader><CardContent>{source.reason}</CardContent></Card>)}</div>
      <Card><CardHeader><CardTitle>ClickHouse tables</CardTitle><CardDescription>Active parts. Rows are physical rows, including any duplicates.</CardDescription></CardHeader><CardContent>
        <Table><TableHeader><TableRow>{["Table", "Rows", "Stored", "Uncompressed", "Parts"].map(label => <TableHead key={label}>{label}</TableHead>)}</TableRow></TableHeader><TableBody>
          {data.tables.map(table => <TableRow key={`${table.database}.${table.name}`}><TableCell>{table.database}.{table.name}</TableCell><TableCell>{table.rows.toLocaleString()}</TableCell><TableCell>{bytes(table.bytes)}</TableCell><TableCell>{bytes(table.uncompressed_bytes)}</TableCell><TableCell>{table.parts}</TableCell></TableRow>)}
        </TableBody></Table></CardContent></Card>
      <Card><CardHeader><CardTitle>Disk and merges</CardTitle></CardHeader><CardContent>{data.disks.map(disk => <p key={disk.name}>{disk.name}: {bytes(disk.free_space)} free of {bytes(disk.total_space)}</p>)}<p>{data.merges.length} active merges · {bytes(data.merges.reduce((n, m) => n + m.memory_usage, 0))} merge memory</p></CardContent></Card>
      <Card><CardHeader><CardTitle>Durable delivery</CardTitle></CardHeader><CardContent>{data.streams.map(stream => <p key={stream.name}>{stream.name}: {stream.messages.toLocaleString()} messages · {bytes(stream.bytes)}</p>)}</CardContent></Card>
      <Card><CardHeader><CardTitle>Postgres control state</CardTitle></CardHeader><CardContent>{data.control_tables.map(table => <p key={table.name}>{table.name}: {bytes(table.bytes)} · approximately {table.estimated_rows.toLocaleString()} rows</p>)}</CardContent></Card>
    </>}
  </div>
}
