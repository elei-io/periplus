"use client"

import { CollectionQueueSummary } from "@/components/collection-queue"
import Link from "next/link"
import { AdmissionWaitSummary } from "@/components/admission-wait"
import { CollectionItems, CollectionArrivals } from "@/components/frontier-items"
import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { extractApiError, responseJson } from "@/lib/api"
import type { Collection, CollectionPage, CollectionHistoryPage } from "@/types/collections"

async function read<T>(path: string, signal: AbortSignal) {
  return responseJson<T>(await fetch(path, { signal: AbortSignal.any([signal, AbortSignal.timeout(35000)]), cache: "no-store" }))
}
function Summary({ item }: { item: Collection }) {
  return <div className="flex flex-col gap-3">
    <div className="flex flex-wrap gap-2"><Badge variant="secondary">{item.source === "current" ? item.status : "Durable history"}</Badge>{item.outcome && <Badge variant="outline">{item.outcome.replaceAll("_", " ")}</Badge>}</div>
    <p className="break-words whitespace-pre-wrap">{item.specification.seed_description || item.specification.seed_urls.join("\n") || "SQL collection"}</p>
    <CardDescription>Supplied pages passed capture checks. Content usefulness and completeness remain unverified.</CardDescription>
    <CardDescription>Depth {item.specification.max_depth} · Up to {item.specification.page_limit} pages · {item.supplied_pages ?? "Unknown"} supplied · {item.failed_pages ?? "Unknown"} failed · {item.consumed_pages ?? "Unknown"} page units consumed</CardDescription>
    {item.source === "current" && <><CardDescription>{item.queued_pages} queued · {item.acquiring_pages} acquiring · {item.selecting_pages} selecting links · {item.reserved_pages} page units reserved</CardDescription><CardDescription>{item.shared_pages} shared associations · {item.reused_pages} recent results reused. These are request results, not physical browser attempts.</CardDescription><p>{item.seeds_settled ? "Starting URL selection has finished." : "Starting URLs are still being selected or admitted; counts may grow."}</p>{item.waiting_reason && <p>Waiting: {item.waiting_reason.replaceAll("_", " ")}</p>}</>}
    <CardDescription>As of {new Date(item.as_of).toLocaleString()}</CardDescription>
  </div>
}
export function CollectionDetail({ id }: { id: string }) {
  const query = useQuery({ queryKey: ["collection", id], queryFn: ({ signal }) => read<Collection>(`/api/collections/${encodeURIComponent(id)}`, signal), refetchInterval: 10000, retry: false })
  const item = query.data
  // The API validates identities. Do not interpolate an unvalidated URL parameter into runnable SQL.
  const sql = item && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(item.id) ? `SELECT o.*, f.depth, f.mode FROM web.fulfillment f JOIN web.observation o USING (observation_id) WHERE f.collection_id = '${item.id}' LIMIT 100;` : null
  return <Card id="request"><CardHeader><CardTitle><h2>Collection details</h2></CardTitle><CardDescription>Keep this page’s URL to return to this request.</CardDescription></CardHeader><CardContent className="flex flex-col gap-4">
    {query.error && <Alert variant="destructive"><AlertDescription>{item ? "Status may be stale. " : "Collection unavailable. "}{extractApiError(query.error)} <Button variant="link" onClick={() => void query.refetch()}>Retry</Button></AlertDescription></Alert>}
    {query.isPending && <p role="status">Loading collection…</p>}
    {item && <><Summary item={item} /><p>{item.query_ready === true ? "Query readiness verified for the complete collection." : item.query_ready === false ? "Collection materialization is pending." : "Query readiness has not been verified."} Settlement does not prove ingestion or materialization completion.</p><CardDescription>{item.query_readiness_reason.replaceAll("_", " ")}{item.query_readiness_as_of && ` · Checked ${new Date(item.query_readiness_as_of).toLocaleString()}`}</CardDescription>
      {item.source === "current" && <><CollectionQueueSummary item={item} /><AdmissionWaitSummary value={item.admission} /><CardDescription>{item.ingested_pages} page evidence commits confirmed · Collection lineage {item.lineage_ready ? "confirmed" : "not yet confirmed"}</CardDescription>{item.discovery_stage && <p>Source discovery: {item.discovery_stage}</p>}{item.resolved_urls.length > 0 && <details><summary>Resolved starting URLs</summary><ul>{item.resolved_urls.map(url => <li key={url} className="break-all">{url}</li>)}</ul></details>}{item.search_queries.length > 0 && <details><summary>Discovery searches</summary><ul>{item.search_queries.map(value => <li key={value}>{value}</li>)}</ul></details>}</>}
      <details><summary>Frozen selection and scope</summary><div className="flex flex-col gap-3"><p>Recent-result age: {item.specification.result_max_age_seconds} seconds</p><p>Allowed sections: {item.specification.allowed_sections.join(", ") || "No request-specific restriction"}</p><pre className="overflow-auto whitespace-pre-wrap break-all">{item.specification.follow_sql}</pre>{item.specification.seed_sql && <pre className="overflow-auto whitespace-pre-wrap break-all">{item.specification.seed_sql}{"\n"}{JSON.stringify(item.specification.seed_parameters)}</pre>}</div></details>
      {sql && <Link className="underline" href={`/discover?${new URLSearchParams({mode: "sql", sql})}`}>Query this collection’s available observations →</Link>}
      {item.source === "current" && <CollectionItems key={item.id} id={item.id} />}<CollectionArrivals key={`arrivals-${item.id}`} id={item.id} /><CardDescription className="break-all">Reference: {item.id}</CardDescription></>}
  </CardContent></Card>
}
function CurrentActivity() {
  const [status, setStatus] = useState<"active" | "paused" | "settled">("active")
  const [offset, setOffset] = useState(0)
  const query = useQuery({ queryKey: ["collections", status, offset], queryFn: ({ signal }) => read<CollectionPage>(`/api/collections?${new URLSearchParams({ status, offset: String(offset), limit: "10" })}`, signal), refetchInterval: 10000, retry: false })
  return <section className="flex flex-col gap-5" aria-labelledby="activity-heading"><CardTitle><h2 id="activity-heading">Public collections</h2></CardTitle><p>Current requests, updated every 10 seconds. Historical requests remain accessible through their saved links.</p>
    <div role="group" aria-label="Filter collections" className="flex flex-wrap gap-2">{(["active", "paused", "settled"] as const).map(value => <Button key={value} variant={status === value ? "secondary" : "outline"} aria-pressed={status === value} onClick={() => {setStatus(value);setOffset(0)}}>{value}</Button>)}</div>
    {query.error && <Alert variant="destructive"><AlertDescription>{query.data ? "Status may be stale. " : "Collections unavailable. "}{extractApiError(query.error)} <Button variant="link" onClick={() => void query.refetch()}>Retry</Button></AlertDescription></Alert>}
    {query.isPending && <p role="status">Loading collections…</p>}
    {query.data?.items.length === 0 && <p>No current collections on this page.</p>}
    {query.data?.items.map(item => <Card key={item.id}><CardContent className="flex flex-col gap-3"><Summary item={item} /><Link className="underline" href={`/suggest?request=${item.id}#request`}>View collection →</Link></CardContent></Card>)}
    <div className="flex gap-3"><Button variant="outline" disabled={!offset || query.isFetching} onClick={() => setOffset(Math.max(0, offset - 10))}>Previous</Button><Button variant="outline" disabled={query.isError || query.isFetching || query.data?.items.length !== 10 || offset >= 10000} onClick={() => setOffset(offset + 10)}>Next</Button></div>
  </section>
}

export function CollectionActivity() {
  const [history, setHistory] = useState(false)
  return <div className="flex flex-col gap-5"><div className="flex flex-wrap gap-2" role="group" aria-label="Collection source"><Button variant={!history ? "secondary" : "outline"} aria-pressed={!history} onClick={() => setHistory(false)}>Current requests</Button><Button variant={history ? "secondary" : "outline"} aria-pressed={history} onClick={() => setHistory(true)}>Durable history</Button></div>{history ? <HistoryActivity /> : <CurrentActivity />}</div>
}
function HistoryActivity() {
  const [cursors, setCursors] = useState<Array<string | null>>([null])
  const cursor = cursors[cursors.length - 1]
  const query = useQuery({ queryKey: ["collection-history", cursor], queryFn: ({ signal }) => read<CollectionHistoryPage>(`/api/collections/history?limit=10${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`, signal), retry: false })
  return <section className="flex flex-col gap-4"><CardTitle><h2>Public collection history</h2></CardTitle><p>Immutable records may arrive before their outcomes. Missing counts remain unknown.</p><Button className="self-start" variant="outline" disabled={query.isFetching} onClick={() => { setCursors([null]); if (cursors.length === 1) void query.refetch() }}>Refresh newest history</Button>
    {query.isPending && <p role="status">Loading history…</p>}
    {query.error && <Alert variant="destructive"><AlertDescription>{query.data ? "History may be stale. " : "History unavailable. "}{extractApiError(query.error)}</AlertDescription></Alert>}
    {query.data && <CardDescription>As of {new Date(query.data.as_of).toLocaleString()}. Restart from the newest page to include newly ingested records.</CardDescription>}
    {query.data?.items.length === 0 && <p>No durable collections on this page.</p>}
    {query.data?.items.map(item => <Card key={item.id}><CardContent className="flex flex-col gap-3"><p className="break-words">{item.summary}</p><Badge variant="secondary">{item.outcome?.replaceAll("_", " ") || "Outcome not yet recorded"}</Badge><CardDescription>{item.supplied_pages ?? "Unknown"} supplied · {item.failed_pages ?? "Unknown"} failed</CardDescription><Link className="underline" href={`/suggest?request=${item.id}#request`}>View collection →</Link></CardContent></Card>)}
    <div className="flex gap-3"><Button variant="outline" disabled={cursors.length === 1 || query.isFetching} onClick={() => setCursors(cursors.slice(0,-1))}>Previous</Button><Button variant="outline" disabled={query.isError || query.isFetching || !query.data?.next_cursor} onClick={() => { if (query.data?.next_cursor) setCursors([...cursors, query.data.next_cursor]) }}>Next</Button></div>
  </section>
}
