import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { useDefinitions, useSchedules, useScheduleAction } from "@/hooks/use-schedules"
import { extractApiError } from "@/lib/api"
import { ScheduleEditor } from "./schedule-editor"
import type { RequestSchedule } from "@/types/schedules"

const date = (value: string | null) => value ? new Date(value).toLocaleString() : "—"
export function ScheduleSummary({ schedule }: { schedule: RequestSchedule }) {
  return <div className="space-y-2">
    <Badge variant="outline">{!schedule.enabled ? "Paused" : schedule.next_at ? "Scheduled" : "Finished"}</Badge>
    <p>{schedule.configuration.kind === "interval" ? `Every ${schedule.configuration.interval_seconds} seconds` : `${schedule.configuration.cron} · ${schedule.configuration.timezone}`}</p>
    <p>Next: {date(schedule.next_at)} · Executions: {schedule.execution_count} / {schedule.configuration.max_count ?? "unlimited"}</p>
    <p className="text-sm text-muted-foreground">Start {date(schedule.configuration.start_at)} · Stop {date(schedule.configuration.stop_at)} · Last tick: {schedule.last_result?.replaceAll("_", " ") ?? "Not yet evaluated"}</p>
    {schedule.last_request_id && <a className="block underline" href={`/observatory/executions/${schedule.last_request_id}`}>Latest execution</a>}
  </div>
}
export function SchedulesPage({ id }: { id?: string }) {
  const definitions = useDefinitions()
  const schedules = useSchedules()
  const action = useScheduleAction()
  const [selected, setSelected] = useState(() => new URLSearchParams(window.location.search).get("request") ?? "")
  const [creating, setCreating] = useState(() => new URLSearchParams(window.location.search).has("request"))
  const [editing, setEditing] = useState<string | null>(null)
  const definition = definitions.data?.find((item) => item.id === selected)
  const items = schedules.data?.filter((item) => !id || item.id === id)
  return <div className="w-full space-y-5">
    <div className="flex items-center justify-between gap-3"><h1 className="text-2xl font-semibold">Schedules</h1><Button onClick={() => { setCreating(true); setEditing(null) }}>New schedule</Button></div>
    <p className="text-muted-foreground">Choose when a saved request runs. Change sources, scope and budgets on the request itself.</p>
    {id && <a className="block underline" href="/observatory/schedules">All schedules</a>}
    {[definitions.error, schedules.error].filter(Boolean).map((error, i) => <p key={i} role="alert">{extractApiError(error)}</p>)}
    {(definitions.isPending || schedules.isPending) && <p role="status">Loading schedules…</p>}
    {creating && <Card><CardHeader><CardTitle>Choose a request</CardTitle></CardHeader><CardContent className="space-y-3">
      <div className="flex flex-wrap gap-2">{definitions.data?.map((item) => <Button key={item.id} variant={selected === item.id ? "secondary" : "outline"} aria-pressed={selected === item.id} onClick={() => setSelected(item.id)}>{item.name}</Button>)}</div>
      {definitions.data?.length === 0 && <p>No saved requests yet. <a className="underline" href="/observatory/requests/new">Create a request</a> first.</p>}
      <Button variant="outline" onClick={() => setCreating(false)}>Cancel</Button>
    </CardContent></Card>}
    {creating && definition && <ScheduleEditor key={definition.id} definition={definition} close={() => setCreating(false)} />}
    {items?.map((schedule) => {
      const request = definitions.data?.find((item) => item.id === schedule.definition_id)
      return <Card key={schedule.id}><CardHeader><CardTitle>Runs request: <a className="underline" href={`/observatory/requests/${schedule.definition_id}`}>{request?.name ?? schedule.definition_id}</a></CardTitle></CardHeader><CardContent className="space-y-4">
        <ScheduleSummary schedule={schedule} />
        <div className="flex gap-2"><Button variant="outline" disabled={!request} onClick={() => { setEditing(schedule.id); setCreating(false) }}>Edit timing</Button>
        <Button variant="outline" disabled={action.isPending} onClick={() => action.mutate({path: `/request-definitions/${schedule.definition_id}/schedules/${schedule.id}`, method: "PUT", body: {...schedule.configuration, enabled: !schedule.enabled, expected_version: schedule.version}})}>{schedule.enabled ? "Pause schedule" : "Resume schedule"}</Button></div>
        {editing === schedule.id && request && <ScheduleEditor key={schedule.id} definition={request} schedule={schedule} close={() => setEditing(null)} />}
      </CardContent></Card>
    })}
    {items?.length === 0 && <p>{id ? "This schedule is not in the loaded schedules." : "No schedules yet. Run requests manually or create a schedule."}</p>}
    <p className="text-xs text-muted-foreground">Showing up to 200 schedules and saved requests. Skipped ticks do not count. Active executions prevent overlap; missed ticks are skipped. Stopping a schedule does not cancel its executions.</p>
  </div>
}
