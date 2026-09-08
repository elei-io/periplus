"use client"

import { useEffect, useLayoutEffect, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { ArrowRight, ArrowUpRight, Check, CornerDownRight, Globe2, Info, Plus, X } from "lucide-react"
import { Button, buttonVariants } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog"
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { Badge } from "@/components/ui/badge"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { CollectionForm } from "@/components/collection-form"
import { RequestStory, PublicRequests, ExploreObservedWeb } from "@/components/observatory-stories"
import { useCaptureStream } from "@/hooks/use-capture-stream"
import { useAnimatedCount } from "@/hooks/use-animated-count"
import { useCaptureCount } from "@/hooks/use-capture-count"
import { extractApiError, responseJson } from "@/lib/api"
import type { LiveView } from "@/types/live"
import type { CockpitItem } from "@/types/cockpit"
import { age } from "@/lib/observation-age"
import { speedometerScale } from "@/lib/cockpit"
import "./crawler-cockpit.css"

async function read<T>(path: string, signal: AbortSignal) {
  return responseJson<T>(await fetch(path, { signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]), cache: "no-store" }))
}



function CaptureCount({ motion }: { motion: boolean }) {
  const query = useCaptureCount(motion ? 5000 : false)
  const value = useAnimatedCount(query.data?.rows[0]?.[0], motion)
  return <div className="crawler-total"><span className="crawler-metric-label">Observations recorded</span><strong>{query.error ? "—" : value !== null ? String(value).replace(/\B(?=(\d{3})+(?!\d))/g, ",") : "…"}</strong><span>{query.error ? "Observation total unavailable" : "Successful observations · includes repeat visits"}</span></div>
}

function Speedometer({ rate }: { rate: number | undefined }) {
  const scale = speedometerScale(rate)
  const rotation = -90 + Math.min(1, Math.max(0, (rate ?? 0) / scale)) * 180
  return <div className="crawler-speed"><svg viewBox="0 0 120 78" aria-hidden="true"><path className="crawler-dial-track" d="M 12 61 A 48 48 0 0 1 108 61" /><path className="crawler-dial-fill" d="M 12 61 A 48 48 0 0 1 108 61" pathLength="100" strokeDasharray={`${Math.min(100, (rate ?? 0) / scale * 100)} 100`} /><g style={{transform: `rotate(${rotation}deg)`, transformOrigin: "60px 61px"}}><line x1="60" y1="61" x2="60" y2="23" /><circle cx="60" cy="61" r="4" /></g><text x="9" y="76">0</text><text x="108" y="76" textAnchor="end">{scale}</text></svg><div><span className="crawler-metric-label">Observation rate</span><strong>{rate === undefined ? "—" : rate.toLocaleString(undefined, {maximumFractionDigits:1})}<small>observations / min</small></strong><span>5-minute average · scale 0–{scale}</span></div></div>
}

export function CrawlerCockpit({ initialId }: { initialId?: string }) {
  const [form, setForm] = useState(false)
  const [requestId, setRequestId] = useState<string | null>(initialId ?? null)
  const [motion, setMotion] = useState(true)
  const stream = useRef<HTMLDivElement>(null)
  const rowPositions = useRef(new Map<string, number>())
  const requestForm = useRef<HTMLElement>(null)
  useEffect(() => { if (form) requestForm.current?.focus() }, [form])
  const live = useQuery({ queryKey: ["crawler-live"], queryFn: ({ signal }) => read<LiveView>("/api/frontier/live", signal), refetchInterval: motion ? 5000 : false, retry: false })
  const captures = useCaptureStream(true, motion)
  const current = live.data?.current
  const recent: CockpitItem[] = captures.visible.map(item => ({ id: item.observation_id, url: item.requested_url, stage: "past", label: `Observed · ${age(item.completed_at, captures.query.data?.as_of ?? current?.as_of ?? item.completed_at)}`, origin: item.query_ready ? "Ready to query" : "Query readiness not yet verified" }))
  useLayoutEffect(() => {
    const nextPositions = new Map<string, number>()
    for (const row of stream.current?.querySelectorAll<HTMLElement>("[data-capture-id]") ?? []) {
      const id = row.dataset.captureId!
      const top = row.offsetTop
      const before = rowPositions.current.get(id)
      if (motion && before !== undefined && before !== top && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        row.animate([{transform: `translateY(${before - top}px)`}, {transform: "translateY(0)"}], {duration: 320, easing: "ease-out"})
      }
      nextPositions.set(id, top)
    }
    rowPositions.current = nextPositions
  })
  const domains = (current?.domains ?? []).filter(item => item.unique_queued_urls > 0)
  const speed = live.data?.history?.velocities.find(value => value.seconds === 300 && value.domain === null)
  const unavailable = !live.data
  const status = live.isError ? "Connection interrupted" : live.isPending ? "Connecting…" : !motion ? "Updates paused" : current?.paused ? "Observations paused" : current?.started ? "Observing the web" : current?.dispatched ? "Preparing observations" : current?.queued ? "Work waiting" : "Awaiting new pages"
  const closeRequest = () => {
    setRequestId(null)
    const url = new URL(window.location.href)
    if (url.searchParams.has("request")) {
      url.searchParams.delete("request")
      if (url.hash === "#request") url.hash = ""
      window.history.replaceState(null, "", url.pathname + url.search + url.hash)
    }
  }
  return <main className={`crawler-cockpit ${!motion ? "crawler-updates-paused" : ""}`}>
    <header className="flex flex-wrap items-baseline justify-between gap-2 py-6"><h1 className="text-xl font-medium">Observatory</h1><p className="text-sm text-muted-foreground">Monitor observations, inspect coverage, and submit a starting URL.</p></header><div className="crawler-heading-actions"><Badge variant="outline"><span className={current?.started ? "crawler-pulse" : "crawler-dot"} />{status}</Badge><Button variant="ghost" size="sm" onClick={() => {setMotion(!motion)}}>{motion ? "Pause updates" : "Resume updates"}</Button></div>
    {live.error && <Alert variant="destructive"><AlertDescription>{live.data ? "Showing the last received snapshot. " : "Live activity is unavailable. "}{extractApiError(live.error)}<Button variant="link" onClick={() => void live.refetch()}>Retry</Button></AlertDescription></Alert>}
    {[captures.query.error].filter(Boolean).map((error, index) => <Alert key={index} variant="destructive"><AlertDescription>Some activity may be unavailable. {extractApiError(error)}</AlertDescription></Alert>)}
    {captures.query.data?.reset_reason && <p className="crawler-footnote" role="status">The live feed resynced after an interruption. Showing the latest observations; earlier results remain in the catalogue.</p>}
    <div className="crawler-workspace" aria-label="Observatory activity">
      <section className="crawler-stream-panel" aria-labelledby="capture-stream-heading">
        <div className="crawler-instruments"><Speedometer rate={speed ? speed.successful_captures / 5 : undefined} /><CaptureCount motion={motion} /></div>
        <header className="crawler-stream-heading"><div><h2 id="capture-stream-heading">Latest observations</h2></div></header>
        <div ref={stream} className="crawler-stream-table"><Table aria-label="Latest observations"><TableHeader><TableRow><TableHead>Page</TableHead><TableHead>Observed</TableHead><TableHead><span className="sr-only">Open website</span></TableHead></TableRow></TableHeader><TableBody>{recent.map(item => <TableRow key={item.id} data-capture-id={item.id} className="crawler-stream-row"><TableCell><a href={item.url} target="_blank" rel="noopener noreferrer" className="crawler-row-url" title={`${item.url} (opens in a new tab)`}><Check size={13} /><span>{item.url.replace(/^https?:\/\//, "")}</span></a></TableCell><TableCell>{item.label.replace(/^Observed · /, "")}</TableCell><TableCell><a className={buttonVariants({ variant: "ghost", size: "icon-sm" })} href={item.url} target="_blank" rel="noopener noreferrer" aria-label={`Open ${item.url} in a new tab`}><ArrowUpRight size={14} /></a></TableCell></TableRow>)}</TableBody></Table>{!recent.length && <div className="crawler-empty">{unavailable ? "Waiting for a live snapshot." : "New observations will appear here as pages are explored."}</div>}</div>
        <footer className="crawler-stream-footer"><span>{current ? `Snapshot ${new Date(current.as_of).toLocaleTimeString()} · updates every 5 seconds` : "Connecting to the observatory"}</span><span>{captures.burst > 1 ? `${captures.burst} observations in the latest burst · ` : ""}Links open the live website in a new tab <TooltipProvider><Tooltip><TooltipTrigger aria-label="About observation metrics" className="crawler-metrics-info"><Info size={13} /></TooltipTrigger><TooltipContent>Totals and rate count successful observations, including repeat visits. Recorded totals may follow a little behind the live feed. The dial adjusts its labeled scale to the current rate; it is not a capacity limit.</TooltipContent></Tooltip></TooltipProvider></span></footer>
        
      </section>
      <aside className="crawler-frontier-panel" aria-labelledby="frontier-heading"><header><span className="crawler-lane-eyebrow">LOOKING AHEAD <ArrowRight size={14} /></span><h2 id="frontier-heading">Where we’re looking next</h2><p>Sites and unique pages awaiting observation</p></header>
        <div className="crawler-domain-list" aria-label="Upcoming sites">{domains.map(domain => <div key={domain.domain} className="crawler-domain"><div><Globe2 size={14} /><strong>{domain.domain}</strong><span>{domain.unique_queued_urls.toLocaleString()}<small>URLs</small></span></div></div>)}{!domains.length && <p className="crawler-empty">{unavailable ? "Waiting for a live snapshot." : "No waiting sites in this list. Suggest a starting point to bring more of the web into view."}</p>}</div>
        <p className="crawler-footnote">{current?.more_domains ? "More sites are waiting beyond this list. " : ""}New requests and discovered links shape what comes next.</p>
    <section className="crawler-request-bar" aria-label="Suggest a starting point"><div><span className="crawler-request-mark"><CornerDownRight size={20} /></span><div><strong>Where should we look next?</strong><p>Suggest a website or describe what you want to explore. Each request has its own progress view.</p></div></div>
      <div className="crawler-request-actions"><Button variant="default" onClick={() => setForm(!form)}><Plus />Suggest a starting point</Button></div>
    </section>
      </aside>
    </div>
    {form && <section ref={requestForm} tabIndex={-1} className="crawler-form"><div className="flex justify-end"><Button variant="ghost" onClick={() => setForm(false)}><X />Close</Button></div><CollectionForm onCreated={id => {setForm(false);setRequestId(id)}} /></section>}

    <Dialog open={requestId !== null} onOpenChange={open => { if (!open) closeRequest() }}>
      <DialogContent className="observatory-request-dialog">
        <DialogHeader className="sr-only"><DialogTitle>Request progress</DialogTitle><DialogDescription>Observation results and scope for this request.</DialogDescription></DialogHeader>
        {requestId && <RequestStory key={requestId} id={requestId} playing/>}
      </DialogContent>
    </Dialog>
    <PublicRequests playing={motion} onOpenRequest={setRequestId}/><ExploreObservedWeb/>
  </main>
}
