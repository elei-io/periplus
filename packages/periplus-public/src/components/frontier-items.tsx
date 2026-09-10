"use client"
import { StartWindow } from "@/components/start-window"
import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { extractApiError } from "@/lib/api"
import type { AcquisitionView, ObservationLineagePage, CollectionItemsPage, CollectionArrivalsPage } from "@/types/frontier-items"

class FrontierReadError extends Error {
  readonly status: number
  constructor(status: number, message: string) { super(message); this.status = status }
}

async function read<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(`/api${path}`, { signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]), cache: "no-store" })
  const body = await response.json()
  if (!response.ok) throw new FrontierReadError(response.status, typeof body?.detail === "string" ? body.detail : "Observation details could not be loaded.")
  return body as T
}
const collectionLink = (id: string) => `/coverage?request=${encodeURIComponent(id)}#request`
const itemLink = (id: string) => `/frontier/${encodeURIComponent(id)}`
function modeLabel(mode: string) { return mode === "acquired" ? "Observation requested" : mode === "shared" ? "Shared work" : mode === "reused" ? "Earlier observation reused" : mode }
function observationLink(id: string) {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)) return null
  return `/sql?${new URLSearchParams({sql:`SELECT * FROM public_v1.capture WHERE capture_id = '${id}' LIMIT 1;`})}`
}
function Observation({ id, label }: { id: string; label: string }) {
  const href = observationLink(id)
  return href ? <a className="break-all underline" href={href}>{label}: {id}</a> : null
}
function Readiness({ item }: { item: Pick<AcquisitionView, "query_ready" | "query_readiness_reason"> }) {
  return <p>{item.query_ready === true ? "Ready to query."
    : item.query_ready === false ? "Being prepared for queries."
    : item.query_readiness_reason === "catalogue_readiness_unavailable" ? "Query availability could not be checked; showing current page status."
    : "Query readiness has not been verified."}</p>
}

export function CollectionItems({ id }: { id: string }) {
  const [cursors, setCursors] = useState<Array<string | null>>([null])
  const cursor = cursors[cursors.length - 1]
  const query = useQuery({ queryKey: ["collection-items", id, cursor], queryFn: ({ signal }) => read<CollectionItemsPage>(`/collections/${encodeURIComponent(id)}/items?limit=10${cursor ? `&after=${encodeURIComponent(cursor)}` : ""}`, signal), refetchInterval: 10000, retry: false })
  return <section className="flex min-w-0 flex-col gap-4"><h2>Pages in this request</h2><p className="text-sm text-muted-foreground">Pages connected to this request, not their order of observation. Refresh the first page to include newly discovered pages.</p>
    <Button variant="outline" className="self-start" disabled={query.isFetching} onClick={() => {setCursors([null]);if(cursors.length === 1) void query.refetch()}}>Refresh page list</Button>
    {query.isPending && <p role="status">Loading pages…</p>}
    {query.error && <p role="alert">{query.data ? "Page details may be out of date. " : "Current pages unavailable. "}{extractApiError(query.error)}</p>}
    {query.data && <p className="text-sm text-muted-foreground">As of {new Date(query.data.as_of).toLocaleString()}</p>}
    {query.data?.items.length === 0 && <p>No pages are listed here yet. Periplus may still be choosing starting URLs or adding pages to this request.</p>}
    {query.data?.items.map(item => <Card key={item.interest_id}><CardHeader><CardTitle><a className="break-all underline" href={itemLink(item.acquisition.id)}>{item.acquisition.url}</a></CardTitle></CardHeader><CardContent className="flex flex-col gap-2"><p>Request work: {item.status.replaceAll("_", " ")} · {modeLabel(item.mode)} · Page unit {item.budget_state}</p><p>Depth {item.context.depth} · Rule {item.context.rule_id} · Observation status {item.acquisition.status}</p>{item.acquisition.waiting_reason && <p>{item.acquisition.waiting_reason.replaceAll("_", " ")}</p>}{item.context.parent_observation_id && <Observation id={item.context.parent_observation_id} label="Parent observation" />}{item.acquisition.observation_id && <><Observation id={item.acquisition.observation_id} label="Observation" /><Readiness item={item.acquisition} /></>}<p className="text-sm text-muted-foreground">Admitted {new Date(item.admitted_at).toLocaleString()}</p></CardContent></Card>)}
    <div className="flex gap-2"><Button variant="outline" disabled={cursors.length === 1 || query.isFetching} onClick={() => setCursors(cursors.slice(0,-1))}>Previous items</Button><Button variant="outline" disabled={query.isError || query.isFetching || !query.data?.next_after} onClick={() => {if(query.data?.next_after) setCursors([...cursors,query.data.next_after])}}>Next items</Button></div>
  </section>
}
export function FrontierItemDetail({ id }: { id: string }) {
  const query = useQuery({ queryKey: ["frontier-item", id], queryFn: ({ signal }) => read<AcquisitionView>(`/frontier/items/${encodeURIComponent(id)}`, signal), refetchInterval: 10000, retry: false })
  const missing = query.error instanceof FrontierReadError && query.error.status === 404
  const item = missing ? undefined : query.data
  return <div className="flex w-full min-w-0 flex-col gap-4"><h1 className="text-2xl font-semibold">Page details</h1><Button className="self-start" variant="outline" disabled={query.isFetching} onClick={() => void query.refetch()}>Refresh page details</Button>
    {query.isPending && <p role="status">Loading page details…</p>}
    {missing && <p>This page is no longer in the active list. Recorded observations and request connections appear below when available.</p>}
    {query.error && !missing && <p role="alert">{item ? "Page status may be out of date. " : "Current page details unavailable. "}{extractApiError(query.error)}</p>}
    {item && <><p className="break-all">{item.url}</p><div><Badge>{item.status}</Badge></div><p>{item.domain} · {item.attempt_count} visits attempted</p><p className="text-sm text-muted-foreground">As of {new Date(item.as_of).toLocaleString()} · Created {new Date(item.created_at).toLocaleString()}</p>
      {item.waiting_reason && <p>Current stage: {item.waiting_reason.replaceAll("_", " ")}</p>}
      {item.eligibility_not_before && <p>Eligible from: {new Date(item.eligibility_not_before).toLocaleString()}. This is the earliest possible time, not a scheduled visit. Site limits and available capacity still apply.</p>}
      {item.terminal_reason && <p>Observation stopped: {item.terminal_reason.replaceAll("_", " ")}.</p>}
      {item.next_start_estimate ? <StartWindow estimate={item.next_start_estimate} /> : <p>Next-start estimate unavailable: {item.estimate_unavailable_reason?.replaceAll("_", " ")}.</p>}
      <p>Observation recorded: {item.evidence_committed ? "confirmed" : "not yet confirmed"}.</p><Readiness item={item} />
      {item.observation_id && <Observation id={item.observation_id} label="Query observation" />}
      <Card><CardHeader><CardTitle>Connected requests</CardTitle></CardHeader><CardContent className="flex flex-col gap-3"><p className="text-sm text-muted-foreground">Requests currently connected to this page. Recorded connections remain available after a request finishes.</p>{item.callers.map(caller => <div key={caller.interest_id}><a className="break-all underline" href={collectionLink(caller.collection_id)}>{caller.collection_id}</a><p>{modeLabel(caller.mode)} · {caller.status.replaceAll("_", " ")}</p></div>)}{item.more_callers && <p>Showing ten connections; more exist.</p>}{!item.callers.length && <p>No public requests currently connected.</p>}</CardContent></Card>
    </>}
  <ObservationLineage key={item?.observation_id ?? id} id={item?.observation_id ?? id} />
</div>
}

export function CollectionArrivals({ id }: { id: string }) {
  const [cursors, setCursors] = useState<Array<string | null>>([null])
  const cursor = cursors[cursors.length - 1]
  const query = useQuery({ queryKey: ["collection-arrivals", id, cursor], queryFn: ({ signal }) => read<CollectionArrivalsPage>(`/collections/${encodeURIComponent(id)}/arrivals?limit=10${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`, signal), refetchInterval: 10000, retry: false })
  return <section className="flex min-w-0 flex-col gap-4"><h2>Recorded results</h2><p className="text-sm text-muted-foreground">Results connected to this request, newest first. They remain available after the request finishes. Refresh the first page to see newly recorded results.</p>
    <Button variant="outline" className="self-start" disabled={query.isFetching} onClick={() => {setCursors([null]); if(cursors.length === 1) void query.refetch()}}>Refresh latest results</Button>
    {query.isPending && <p role="status">Loading results…</p>}
    {query.error && <p role="alert">{query.data ? "Arrivals may be stale. " : "Arrivals unavailable. "}{extractApiError(query.error)}</p>}
    {query.data && <p className="text-sm text-muted-foreground">As of {new Date(query.data.as_of).toLocaleString()}</p>}
    {query.data && !query.data.definition_committed && <p>The request details are still being saved for later queries.</p>}
    {query.data?.definition_committed && query.data.items.length === 0 && <p>No saved results are listed here yet. Page results may still be processing.</p>}
    {query.data?.items.map(item => <Card key={item.fulfillment_id}><CardHeader><CardTitle><span className="break-all">{item.requested_url}</span></CardTitle></CardHeader><CardContent className="flex flex-col gap-2"><p>{item.mode === "acquired" ? "New observation" : item.mode === "shared" ? "Shared result" : "Reused result"} · Depth {item.depth} · Rule {item.rule_id}</p><p>Result connected {new Date(item.decided_at).toLocaleString()}</p><p>{item.observation_committed ? `Observation recorded: ${item.outcome ?? "outcome unavailable"}${item.http_status_code === null ? "" : ` · HTTP ${item.http_status_code}`}` : "Observation not yet confirmed in the catalogue."}</p>{item.effective_url && <p className="break-all">Effective URL: {item.effective_url}</p>}<p>{item.query_ready === true ? "Ready to query." : item.query_ready === false ? "Being prepared for queries." : "Query readiness has not been verified."}</p><Observation id={item.observation_id} label="Query observation" />{item.parent_observation_id && <Observation id={item.parent_observation_id} label="Found through observation" />}</CardContent></Card>)}
    <div className="flex gap-2"><Button variant="outline" disabled={cursors.length === 1 || query.isFetching} onClick={() => setCursors(cursors.slice(0,-1))}>Previous results</Button><Button variant="outline" disabled={query.isError || query.isFetching || !query.data?.next_cursor} onClick={() => {if(query.data?.next_cursor) setCursors([...cursors,query.data.next_cursor])}}>Next results</Button></div>
  </section>
}

function ObservationLineage({ id }: { id: string }) {
  const [cursors, setCursors] = useState<Array<string | null>>([null])
  const cursor = cursors[cursors.length - 1]
  const query = useQuery({
    queryKey: ["observation-lineage", id, cursor],
    queryFn: ({ signal }) => read<ObservationLineagePage>(`/frontier/observations/${encodeURIComponent(id)}/lineage?limit=10${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`, signal),
    refetchInterval: 10000, retry: false,
  })
  const missing = query.error instanceof FrontierReadError && query.error.status === 404
  return <section className="flex min-w-0 flex-col gap-4">
    <h2>Why this page was observed</h2>
    <p>See why this page was observed and which requests used the result. A request may have prompted the visit, shared it, or reused an earlier observation. More connections may appear as results are recorded.</p>
    <Button variant="outline" className="self-start" disabled={query.isFetching} onClick={() => { setCursors([null]); if (cursors.length === 1) void query.refetch() }}>Refresh connections</Button>
    {query.isPending && <p role="status">Loading connections…</p>}
    {missing && <p>No saved page result is available yet.</p>}
    {query.error && !missing && <p role="alert">{query.data ? "Connections may be out of date. " : "Connections unavailable. "}{extractApiError(query.error)}</p>}
    {query.data && <><p className="break-all">{query.data.requested_url}</p><p className="text-sm text-muted-foreground">As of {new Date(query.data.as_of).toLocaleString()}</p><Observation id={query.data.observation_id} label="Query observation" /></>}
    {query.data?.items.length === 0 && <p>No request connections are listed here yet. Results may still be processing.</p>}
    {query.data?.items.map(item => <Card key={`${item.kind}:${item.record_id}`}><CardHeader><CardTitle>{item.kind === "reason" ? "Reason for observation" : "Result use"}</CardTitle></CardHeader><CardContent className="flex flex-col gap-2">
      <p>{item.kind === "reason" ? "Request" : item.mode === "reused" ? "Reused result" : item.mode === "shared" ? "Shared result" : "New observation"}</p>
      {item.collection_id && <a className="break-all underline" href={collectionLink(item.collection_id)}>Request: {item.collection_id}</a>}
      <p>Rule {item.rule_id}{item.depth !== null ? ` · Depth ${item.depth}` : ""}{item.policy_version !== null ? ` · Policy ${item.policy_version}` : ""}</p>
      {item.parent_observation_id && <a className="break-all underline" href={itemLink(item.parent_observation_id)}>Parent observation: {item.parent_observation_id}</a>}
      <p>Recorded {new Date(item.decided_at).toLocaleString()}</p>
    </CardContent></Card>)}
    <div className="flex gap-2"><Button variant="outline" disabled={cursors.length === 1 || query.isFetching} onClick={() => setCursors(cursors.slice(0, -1))}>Previous connections</Button><Button variant="outline" disabled={query.isError || query.isFetching || !query.data?.next_cursor} onClick={() => { if (query.data?.next_cursor) setCursors([...cursors, query.data.next_cursor]) }}>Next connections</Button></div>
  </section>
}
