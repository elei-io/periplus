"use client"
import { StartWindow } from "@/components/start-window"
import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card"
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table"
import { responseJson, extractApiError } from "@/lib/api"
import type { LiveView, RecentCapture } from "@/types/live"

function captureLink(item: RecentCapture) {
  return `/frontier/${encodeURIComponent(item.observation_id)}`
}
export function LivePage() {
  const [seconds, setSeconds] = useState<60 | 300>(300)
  const query = useQuery({ queryKey: ["crawler-live"], queryFn: ({signal}) => fetch('/api/frontier/live', {signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]), cache: 'no-store'}).then(responseJson<LiveView>), refetchInterval: 10000, retry: false })
  const value = query.data
  const rows = value?.history?.velocities.filter(row => row.seconds === seconds) ?? []
  return <main className="flex flex-col gap-6 p-6"><div className="flex flex-wrap items-center gap-3"><h1>Crawler Live</h1><Button variant="outline" disabled={query.isFetching} onClick={() => void query.refetch()}>Refresh live status</Button></div><p>One crawler, many domains. This view shows public work and updates every 10 seconds.</p>
    {query.isPending && <p role="status">Loading crawler activity…</p>}
    {query.error && <p role="alert">{value ? "Live status may be stale. " : "Live status unavailable. "}{extractApiError(query.error)}</p>}
    {value && <><div><Badge>{value.current.paused ? 'New dispatches paused' : 'Dispatch enabled'}</Badge></div><p className="text-sm text-muted-foreground">Current work as of {new Date(value.current.as_of).toLocaleString()}. Started work may still be settling; these counters do not prove worker liveness.</p>
      <Card><CardHeader><CardTitle>Worker availability</CardTitle></CardHeader><CardContent className="flex flex-col gap-2">
        {value.workers.state === 'observed' ? <><p>{value.workers.reported_workers} recent worker reports: {value.workers.ready_workers} with passed dependency checks, {value.workers.blocked_workers} waiting, {value.workers.checking_workers} checking, {value.workers.unknown_workers} with unknown dependency health.</p>{value.workers.waiting_reasons.map(reason => <p key={reason}>Waiting for {reason === 'storage_unavailable' ? 'repository storage' : reason === 'cdp_unavailable' ? 'the page acquisition service' : 'ingestion delivery'}.</p>)}<p>Passed checks do not promise an immediate start; pauses, budgets, domain limits, and occupied workers still apply.</p></> : <p role="status">{value.workers.state === 'unavailable' ? 'Worker reports are unavailable.' : 'No recent worker reports.'} Worker availability is unknown.</p>}
        {!!value.workers.excluded_reports && <p>{value.workers.excluded_reports} stale or invalid reports excluded.</p>}
        {value.workers.more_workers && <p>Showing a bounded worker sample; these are not global worker totals.</p>}
        <p className="text-sm text-muted-foreground">Reports read as of {new Date(value.workers.as_of).toLocaleString()}.</p>
      </CardContent></Card>
      <div className="grid gap-4 sm:grid-cols-3">{[['Queued acquisitions', value.current.queued], ['Dispatched acquisitions', value.current.dispatched], ['Started, awaiting settlement', value.current.started]].map(([label,count]) => <Card key={String(label)}><CardHeader><CardTitle>{label}</CardTitle></CardHeader><CardContent>{count}</CardContent></Card>)}</div>
      <p>Oldest public queue admission: {value.current.oldest_wait_at ? new Date(value.current.oldest_wait_at).toLocaleString() : 'No queued public work'}.</p>
      <section className="flex flex-col gap-3"><h2>Current domains</h2><Table><TableHeader><TableRow><TableHead>Domain</TableHead><TableHead>Queued</TableHead><TableHead>Dispatched</TableHead><TableHead>Started</TableHead></TableRow></TableHeader><TableBody>{value.current.domains.map(row => <TableRow key={row.domain}><TableCell>{row.domain}</TableCell><TableCell>{row.queued}</TableCell><TableCell>{row.dispatched}</TableCell><TableCell>{row.started}</TableCell></TableRow>)}</TableBody></Table>{!value.current.domains.length && <p>No current public domains.</p>}{value.current.more_domains && <p>Showing ten domains; more public domains have work.</p>}</section>
      <section className="flex flex-col gap-3"><h2>Recorded acquisition velocity</h2><p>Committed evidence only; ingestion can lag. Attempt starts count retries separately. Request results count sharing and reuse separately from physical work.</p>{!value.history && <p role="alert">Historical activity is unavailable. Rates and durable recent history are unknown; current work remains visible.</p>}
        <div className="flex gap-2">{([60,300] as const).map(window => <Button key={window} variant={seconds === window ? 'secondary' : 'outline'} aria-pressed={seconds === window} onClick={() => setSeconds(window)}>{window / 60} minute{window === 60 ? '' : 's'}</Button>)}</div>
        {value.history && <><p className="text-sm text-muted-foreground">Window {new Date(new Date(value.history.window_end).getTime()-seconds*1000).toLocaleTimeString()}–{new Date(value.history.window_end).toLocaleTimeString()}. Read as of {new Date(value.history.as_of).toLocaleString()}. Domain preview includes up to ten domains per interval.</p><Table><TableHeader><TableRow><TableHead>Scope</TableHead><TableHead>Attempt starts / min</TableHead><TableHead>Captures succeeded</TableHead><TableHead>Captures failed</TableHead><TableHead>Request results</TableHead></TableRow></TableHeader><TableBody>{rows.map(row => <TableRow key={row.domain ?? 'global'}><TableCell>{row.domain ?? 'All public domains'}</TableCell><TableCell>{row.attempt_starts_per_minute.toLocaleString(undefined,{maximumFractionDigits:2})}</TableCell><TableCell>{row.successful_captures}</TableCell><TableCell>{row.failed_captures}</TableCell><TableCell>{row.fulfillments}</TableCell></TableRow>)}</TableBody></Table></>}
      </section>
      <section className="flex flex-col gap-3"><h2>Latest successful public captures</h2>{!value.recent.length && <p>{value.history ? 'No successful public captures recorded yet.' : 'No retained captures to show; durable history is unavailable.'}</p>}{value.recent.map(item => <Card key={item.observation_id}><CardContent className="flex flex-col gap-2"><a className="break-all underline" href={captureLink(item)}>{item.requested_url}</a><p>Capture completed {new Date(item.completed_at).toLocaleString()}</p><p>{item.evidence_committed ? 'Evidence committed' : 'Ingestion not yet confirmed'} · {item.query_ready === true ? 'Query readiness verified for the active generation' : item.query_ready === false ? 'Materialization pending' : 'Query readiness unverified'}</p></CardContent></Card>)}</section>
      <section className="flex flex-col gap-3"><h2>Upcoming work preview</h2><p>The oldest pending public items, not a promised dispatch order. Priorities, domain permits, pacing, budgets, and new admissions can change what starts next.</p>{value.current.next_start_estimate ? <StartWindow estimate={value.current.next_start_estimate} /> : <p>Start estimates unavailable: {value.current.estimate_unavailable_reason?.replaceAll('_',' ')}.</p>}{value.current.upcoming.map(item => <Card key={item.acquisition_id}><CardContent className="flex flex-col gap-2"><a className="break-all underline" href={`/frontier/${item.acquisition_id}`}>{item.requested_url}</a><p>Admitted {new Date(item.admitted_at).toLocaleString()} · Retry floor {new Date(item.retry_not_before).toLocaleString()}</p></CardContent></Card>)}{!value.current.upcoming.length && <p>No pending public work to preview.</p>}</section>
    </>}
  </main>
}
