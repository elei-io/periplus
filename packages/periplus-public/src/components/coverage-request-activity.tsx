"use client"

import Link from "next/link"
import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { extractApiError, responseJson } from "@/lib/api"
import type { CoverageRequest, CoverageRequestPage, CoverageRequestStatus } from "@/types/coverage-requests"

const labels: Record<CoverageRequestStatus, string> = { pending: "Pending", resolving: "Finding sources", ongoing: "Collecting", completed: "Recently completed", failed: "Needs attention" }

function RequestSummary({ request }: { request: CoverageRequest }) {
  const progress = request.progress
  const finished = progress ? Math.max(0, progress.request_count - progress.pending_request_count) : 0
  return <div className="flex flex-col gap-3">
    <div className="flex flex-wrap items-center justify-between gap-2"><Badge variant="secondary">{request.status === "completed" ? "Collection finished" : labels[request.status]}</Badge><CardDescription><time dateTime={request.created_at}>{new Date(request.created_at).toLocaleString()}</time></CardDescription></div>
    {progress && <CardDescription>{finished.toLocaleString()} {finished === 1 ? "page" : "pages"} finished · {progress.pending_request_count.toLocaleString()} outstanding · {progress.failed_request_count.toLocaleString()} failed{progress.crawl_limit_reached ? " · Page budget reached" : ""}{progress.status === "paused" ? " · Paused" : ""}</CardDescription>}
    {request.error && <Alert variant={request.status === "failed" ? "destructive" : "default"}><AlertDescription>{request.error}</AlertDescription></Alert>}
    <p className="break-words whitespace-pre-wrap">{request.input}</p>
    <CardDescription>{request.kind === "description" ? "Topic request" : "URL request"} · Depth {request.depth} · {request.depth === 0 ? "No links followed" : `${request.link_scope === "both" ? "Internal & external" : request.link_scope === "internal" ? "Internal" : "External"} links`} · Up to {request.max_pages.toLocaleString()} {request.max_pages === 1 ? "page" : "pages"}</CardDescription>
  </div>
}

export function CoverageRequestDetail({ id }: { id: string }) {
  const query = useQuery({ queryKey: ["coverage-request", id], queryFn: async () => responseJson<CoverageRequest>(await fetch(`/api/coverage-requests/${encodeURIComponent(id)}`)), refetchInterval: 10000 })
  return <Card id="request"><CardHeader><CardTitle><h2>Request details</h2></CardTitle><CardDescription>Keep this page’s URL to return to this request.</CardDescription></CardHeader><CardContent className="flex flex-col gap-4">
    {query.error && <Alert variant="destructive"><AlertDescription>{extractApiError(query.error)} <Button variant="link" onClick={() => query.refetch()}>Retry</Button></AlertDescription></Alert>}
    {query.isPending && <p role="status">Loading request…</p>}
    {query.data && <><RequestSummary request={query.data} /><CardDescription>{query.data.status === "pending" ? "Waiting for automatic processing. No approval is needed." : query.data.status === "resolving" ? "Finding relevant public starting pages using search." : query.data.status === "ongoing" ? "Collection is queued or in progress. Counts update automatically." : query.data.status === "failed" ? "This request needs attention. See the explanation above." : "Collection has finished. Some pages may have failed, and data may still be processing before it is queryable."}</CardDescription>{query.data.resolved_urls.length > 0 && <details><summary>Starting pages ({query.data.resolved_urls.length})</summary><ul className="flex flex-col gap-2 py-3">{query.data.resolved_urls.map(url => <li key={url} className="break-all"><a className="story-link" href={url} target="_blank" rel="noopener noreferrer">{url}</a></li>)}</ul></details>}
      {query.data.search_queries.length > 0 && <details><summary>Search queries</summary><ul className="flex flex-col gap-2 py-3">{query.data.search_queries.map(text => <li key={text}>{text}</li>)}</ul></details>}
      {query.data.run_id && <Link className="story-link" href={`/discover?${new URLSearchParams({mode: "sql", sql: `SELECT * FROM web.observation WHERE crawl_id = '${query.data.run_id}' LIMIT 100;`})}`}>Explore collected observations →</Link>}
      <CardDescription>Reference: {id}</CardDescription></>}
  </CardContent></Card>
}

export function CoverageRequestActivity() {
  const [status, setStatus] = useState<CoverageRequestStatus>("pending")
  const [offset, setOffset] = useState(0)
  const query = useQuery({
    queryKey: ["coverage-requests", status, offset],
    queryFn: async () => responseJson<CoverageRequestPage>(await fetch(`/api/coverage-requests?${new URLSearchParams({ status, offset: String(offset), limit: "10" })}`)),
    refetchInterval: 10000,
  })
  return <section className="flex flex-col gap-5" aria-labelledby="activity-heading">
    <div className="flex flex-col gap-2"><CardTitle><h2 id="activity-heading">Coverage requests</h2></CardTitle><p className="text-muted-foreground">A shared view of what people want to explore. Updates every 10 seconds.</p></div>
    <div role="group" aria-label="Filter requests by status" className="flex flex-wrap gap-2">{(Object.keys(labels) as CoverageRequestStatus[]).map(value => <Button key={value} variant={status === value ? "secondary" : "outline"} aria-pressed={status === value} onClick={() => { setStatus(value); setOffset(0) }}>{labels[value]}</Button>)}</div>
    {status === "completed" && <CardDescription>Requests completed in the last 30 days.</CardDescription>}
    {query.error && <Alert variant="destructive"><AlertDescription>{extractApiError(query.error)} <Button variant="link" onClick={() => query.refetch()}>Retry</Button></AlertDescription></Alert>}
    {query.isPending && <p role="status">Loading requests…</p>}
    {query.data && <>
      {!query.data.items.length && <Card><CardContent><p>{status === "pending" ? "No pending requests yet. Suggest the first corner of the web you’d like to explore." : status === "ongoing" ? "No requests are being collected right now." : status === "resolving" ? "No requests are finding sources right now." : status === "failed" ? "No requests need attention." : "No requests have been completed in the last 30 days."}</p></CardContent></Card>}
      {query.data.items.map(request => <Card key={request.id}><CardContent className="flex flex-col gap-3"><RequestSummary request={request} /><Link className="story-link self-start" href={`/suggest?request=${request.id}#request`}>View request →</Link></CardContent></Card>)}
      {query.data.total > 0 && <div className="flex flex-wrap items-center justify-between gap-3"><CardDescription>{offset + 1}–{Math.min(offset + 10, query.data.total)} of {query.data.total}</CardDescription><div className="flex gap-2"><Button variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 10))}>Previous</Button><Button variant="outline" disabled={offset + 10 >= query.data.total} onClick={() => setOffset(offset + 10)}>Next</Button></div></div>}
    </>}
  </section>
}
