"use client"

import Link from "next/link"
import { useLayoutEffect, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { ArrowUpRight, Check } from "lucide-react"
import { Button, buttonVariants } from "@/components/ui/button"
import { Table, TableBody, TableRow, TableCell } from "@/components/ui/table"
import { useRequestObservationTail } from "@/hooks/use-request-observation-tail"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { extractApiError, responseJson } from "@/lib/api"
import { age } from "@/lib/observation-age"
import { datasetSqlUrl, coverageSql } from "@/lib/datasets"
import type { Collection, CollectionPage, CollectionHistoryPage } from "@/types/collections"

async function read<T>(path: string, signal: AbortSignal): Promise<T> {
  return responseJson<T>(await fetch(path, {signal:AbortSignal.any([signal,AbortSignal.timeout(15000)]), cache:"no-store"}))
}
function title(item: Collection) { return item.specification.seed_description || item.specification.seed_urls[0] || "Explore the web" }
function site(url: string) { try { return new URL(url).hostname } catch { return url } }
function queryUrl(id: string) {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)) return null
  return datasetSqlUrl({sql: `SELECT o.requested_url, o.observed_at, o.outcome, f.mode\nFROM web.fulfillment f JOIN web.observation o USING (observation_id)\nWHERE f.collection_id = '${id}'\nORDER BY o.observed_at DESC NULLS LAST LIMIT 100;`})
}
function ReadError({ error }: {error: unknown}) { return <Alert variant="destructive"><AlertDescription>{extractApiError(error)}</AlertDescription></Alert> }
function progress(item: Collection) {
  if (item.source === "history" && item.outcome === null) return "The latest outcome has not been recorded yet. Any available results can be explored below."
  if (item.source === "history" || item.status === "settled") return "This request has finished. Its recorded observations remain available to explore."
  if (item.status === "paused") return "This request is paused. Observations already recorded remain available."
  if (!item.seeds_settled) return "Finding starting pages for this request. Its reach may grow as links are discovered."
  if (item.acquiring_pages) return "Pages are being observed now. New links may bring more of the web into this request."
  if (item.queued_pages) return "Pages are waiting for their turn alongside other public requests."
  return "Checking discovered links and recording progress."
}
function RequestObservationTail({id, playing}: {id:string;playing:boolean}) {
  const {items, query}=useRequestObservationTail(id, playing)
  const tail=useRef<HTMLElement>(null)
  const positions=useRef(new Map<string,number>())
  useLayoutEffect(()=>{
    const next=new Map<string,number>()
    const reduceMotion=window.matchMedia("(prefers-reduced-motion: reduce)").matches
    for (const row of tail.current?.querySelectorAll<HTMLElement>("[data-observation-id]") ?? []) {
      const identity=row.dataset.observationId!
      const top=row.offsetTop
      const before=positions.current.get(identity)
      next.set(identity,top)
      if (!playing || reduceMotion) continue
      if (before === undefined) {
        row.animate([{opacity:0,transform:"translateY(-18px)"},{opacity:1,transform:"translateY(0)"}],{duration:420,easing:"cubic-bezier(.2,.8,.2,1)",fill:"backwards"})
      } else if (before !== top) {
        row.animate([{transform:`translateY(${before-top}px)`},{transform:"translateY(0)"}],{duration:420,easing:"cubic-bezier(.2,.8,.2,1)"})
      }
    }
    positions.current=next
  })
  return <section ref={tail} className="request-observation-tail" aria-label="Latest observations for this request">
    <header><h4>Latest observations</h4><span>{query.isError ? "Updates interrupted" : playing ? "Updates every 5s" : "Updates paused"}</span></header>
    {query.error && <p role="alert">{query.data ? "Showing the last received observations. " : "Observations unavailable. "}{extractApiError(query.error)}<Button variant="link" size="sm" onClick={()=>void query.refetch()}>Retry</Button></p>}
    {query.isPending && <p role="status">Loading observations…</p>}
    {query.data && !items.length && <p>Observations will appear here as this request’s results are recorded.</p>}
    {items.length > 0 && <Table aria-label="Recent pages connected to this request"><TableBody>{items.map(item=><TableRow key={item.observation_id} data-observation-id={item.observation_id} className="request-observation-row"><TableCell><a href={item.requested_url} target="_blank" rel="noopener noreferrer" title={`${item.requested_url} (opens in a new tab)`}><span>{item.requested_url.replace(/^https?:\/\//, "")}</span><ArrowUpRight size={13}/></a></TableCell><TableCell>{item.observed_at ? <time dateTime={item.observed_at} title={new Date(item.observed_at).toLocaleString()}>{age(item.observed_at, query.data?.as_of ?? item.observed_at)}</time> : <span>{item.observation_committed ? "Time unknown" : "Recording"}</span>}{item.outcome && item.outcome !== "succeeded" ? <small>Unsuccessful</small> : item.mode !== "acquired" ? <small>{item.mode === "reused" ? "Reused" : "Shared"}</small> : null}</TableCell></TableRow>)}</TableBody></Table>}
    <p className="request-tail-note">Newest added first · times show when pages were observed. Latest results may still be arriving.</p>
  </section>
}

export function RequestStory({id, playing}: {id:string;playing:boolean}) {
  const query = useQuery({queryKey:["collection",id],queryFn:({signal})=>read<Collection>(`/api/collections/${encodeURIComponent(id)}`,signal),refetchInterval:playing ? 5000 : false,retry:false})
  const item=query.data
  if (!item) return <section className="observatory-request-story">{query.error ? <ReadError error={query.error}/> : <p role="status">Loading request progress…</p>}</section>
  const finished=item.source === "history" ? item.outcome !== null : item.status === "settled"
  const paused=item.source === "current" && item.status === "paused"
  const state=item.retention_expired ? "Retention expired" : finished ? "Finished" : paused ? "Paused" : item.source === "history" ? "Outcome pending" : !item.seeds_settled ? "Finding starting pages" : "In progress"
  const seed=item.specification.seed_urls[0]
  const name=item.specification.seed_description || (seed ? site(seed) : "Web exploration")
  const sql=queryUrl(id)
  const depth=item.specification.max_depth
  return <section className="observatory-request-story">
    {query.error && <ReadError error={query.error}/>}
    <div className="request-summary-kicker"><span>Observation request</span><span className="request-summary-state">{finished ? <Check size={13}/> : <span className="crawler-dot"/>}{state}</span></div>
    <h3 className="request-summary-title">{name}</h3>
    {seed && <a className="request-summary-source" href={seed} target="_blank" rel="noopener noreferrer" title={seed}><span>{seed.replace(/^https?:\/\//, "")}</span><ArrowUpRight size={13}/></a>}
    <div className="request-summary-columns"><div className="request-summary-overview">
    <div className="request-summary-result"><div className="request-summary-count"><strong>{item.supplied_pages?.toLocaleString() ?? "—"}</strong><span>{item.supplied_pages === 1 ? "page observed" : "pages observed"}</span></div><p>{finished ? item.supplied_pages === 0 ? "This request finished without any successful observations." : item.supplied_pages === null ? "This request has finished. Explore any observations recorded so far." : "Observations are kept according to this request’s retention period." : progress(item)}</p></div>
    <div className="request-summary-details">
      {!finished && item.source === "current" && <div className="request-summary-live"><span><strong>{item.acquiring_pages.toLocaleString()}</strong> observing now</span><span><strong>{item.queued_pages.toLocaleString()}</strong> waiting</span></div>}
      {Boolean(item.failed_pages) && <p>{item.failed_pages?.toLocaleString()} {item.failed_pages === 1 ? "page could" : "pages could"} not be observed.</p>}
      <dl className="request-summary-scope"><div><dt>Reach</dt><dd>{depth === 0 ? "Starting pages only" : `Up to ${depth} link ${depth === 1 ? "step" : "steps"}`}</dd></div><div><dt>Page budget</dt><dd>{item.specification.page_limit.toLocaleString()} pages</dd></div><div><dt>Retention</dt><dd>{item.retention_expired ? "Expired · results may be removed" : item.expires_at ? `Until ${new Date(item.expires_at).toLocaleDateString()}` : item.specification.retention_seconds === null ? "Forever" : `${Math.ceil(item.specification.retention_seconds / 86400)} days after completion`}</dd></div></dl>
      {item.source === "current" && (item.shared_pages > 0 || item.reused_pages > 0) && <p>{item.shared_pages.toLocaleString()} shared connections · {item.reused_pages.toLocaleString()} earlier observations reused. These counts can overlap with the pages observed total.</p>}
    </div>
    </div><RequestObservationTail key={id} id={id} playing={playing}/></div>
    <footer className="request-summary-footer"><p>{item.query_ready === true ? "Ready to explore in SQL" : item.supplied_pages === null ? "Explore any results recorded so far" : item.supplied_pages === 0 ? "No observations recorded yet" : "Newest observations may still be arriving"}</p>{sql && <Link className={buttonVariants({variant:"default"})} href={sql}>Explore observations <ArrowUpRight size={14}/></Link>}</footer>
  </section>
}

export function PublicRequests({playing, onOpenRequest}: {playing:boolean;onOpenRequest:(id:string)=>void}) {
  const [offset,setOffset]=useState(0)
  const [history,setHistory]=useState(false)
  const [cursors,setCursors]=useState<Array<string|null>>([null])
  const cursor=cursors[cursors.length-1]
  const current=useQuery({queryKey:["observatory-requests",offset],queryFn:({signal})=>read<CollectionPage>(`/api/collections?limit=6&offset=${offset}`,signal),enabled:!history,refetchInterval:playing ? 10000 : false,retry:false})
  const earlier=useQuery({queryKey:["observatory-request-history",cursor],queryFn:({signal})=>read<CollectionHistoryPage>(`/api/collections/history?limit=6${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,signal),enabled:history,retry:false})
  const query=history ? earlier : current
  const requests=history ? earlier.data?.items.map(item=>({id:item.id,title:item.summary,count:item.supplied_pages,status:item.outcome === null ? "Outcome pending" : "Recorded request",summary:item.supplied_pages === null ? "Results have not been reported yet. Follow the request for available progress." : item.supplied_pages === 0 ? "No successful observations recorded." : item.failed_pages ? `${item.failed_pages} pages could not be observed. Recorded results remain available.` : "Recorded observations remain available to explore."}))
    : current.data?.items.map(item=>({id:item.id,title:title(item),count:item.supplied_pages,status:item.status === "settled" ? "Finished" : item.status === "paused" ? "Paused" : "In progress",summary:item.status === "settled" ? item.failed_pages ? `${item.failed_pages} pages could not be observed. Recorded results remain available.` : "Recorded observations remain available." : item.status === "paused" ? "Waiting for this request to resume." : item.queued_pages ? `${item.queued_pages.toLocaleString()} pages waiting to be observed.` : !item.seeds_settled ? "Finding starting pages." : item.acquiring_pages ? "Observing pages now." : "Following links and recording progress."}))
  const hasPrevious=history ? cursors.length>1 : offset>0
  const hasNext=history ? Boolean(earlier.data?.next_cursor) : current.data?.items.length===6 && offset<10000
  return <section className="observatory-public-requests" aria-labelledby="public-requests-heading"><header className="observatory-section-heading"><div><span className="eyebrow">A shared view</span><h2 id="public-requests-heading">Where people are asking us to look</h2><p>Each starting point can bring new pages into view. Open a request to see its progress and recorded observations.</p></div></header>
    <div className="observatory-request-filters" role="group" aria-label="Request period"><Button variant={history ? "ghost" : "secondary"} size="sm" aria-pressed={!history} onClick={()=>setHistory(false)}>Recent requests</Button><Button variant={history ? "secondary" : "ghost"} size="sm" aria-pressed={history} onClick={()=>setHistory(true)}>Recorded requests</Button></div>
    {query.isPending && <p role="status">Loading public requests…</p>}{query.error && <ReadError error={query.error}/>}{requests?.length===0 && <p>No requests on this page yet. Suggest a starting point above.</p>}
    <div className="observatory-request-list">{requests?.map(item=><article key={item.id}><div className="observatory-request-intent"><span className="observatory-request-state">{item.status === "Finished" ? <Check size={13}/> : <span className="crawler-dot"/>}{item.status}</span><h3>{item.title}</h3><p>{item.summary}</p></div><div className="observatory-request-result"><strong>{item.count?.toLocaleString() ?? "—"}</strong><span>{item.count === 1 ? "page observed" : "pages observed"}</span><Button variant="link" onClick={()=>onOpenRequest(item.id)}>View progress <ArrowUpRight/></Button></div></article>)}</div>
    {(hasPrevious || hasNext) && <div className="observatory-story-actions"><Button variant="outline" disabled={!hasPrevious || query.isFetching} onClick={()=>history ? setCursors(cursors.slice(0,-1)) : setOffset(Math.max(0,offset-6))}>Previous requests</Button><Button variant="outline" disabled={query.isError || query.isFetching || !hasNext} onClick={()=>history ? earlier.data?.next_cursor && setCursors([...cursors,earlier.data.next_cursor]) : setOffset(offset+6)}>More requests</Button></div>}
  </section>
}

export function ExploreObservedWeb() {
  return <section className="observatory-explore" id="coverage"><div><span className="eyebrow">From observation to understanding</span><h2>Explore the observed web</h2><p>Every observation is a view of a page at a moment in time. Ask a question, inspect the recorded sites in SQL, or learn how these observations fit together.</p></div><div className="observatory-explore-links"><Link href="/discover">Ask a question <ArrowUpRight/></Link><Link href={datasetSqlUrl({sql:coverageSql})}>Explore sites and dates in SQL <ArrowUpRight/></Link><Link href="/docs#how-it-works">Understand observations and time <ArrowUpRight/></Link></div></section>
}
