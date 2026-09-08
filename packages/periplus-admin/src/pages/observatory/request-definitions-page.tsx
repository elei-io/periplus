import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card"
import { useDefinitions, useSchedules, useScheduleAction } from "@/hooks/use-schedules"
import { useCollections } from "@/hooks/use-collections"
import { extractApiError } from "@/lib/api"
import { NewCollectionPage } from "@/pages/collections/new-collection-page"
import { ScheduleSummary } from "./schedules-page"

export function RequestDefinitionsPage({ id }: { id?: string }) {
  const definitions = useDefinitions()
  const schedules = useSchedules()
  const action = useScheduleAction()
  const [editing, setEditing] = useState(false)
  const request = definitions.data?.find((item) => item.id === id)
  if (editing && request) return <div className="w-full space-y-4"><Button variant="outline" onClick={() => setEditing(false)}>Cancel editing</Button><NewCollectionPage key={request.id} reusable definition={request} /></div>
  return <div className="w-full space-y-5">
    <div className="flex flex-wrap items-center justify-between gap-3"><h1 className="text-2xl font-semibold">{request?.name ?? "Requests"}</h1><div className="flex gap-4"><a className="underline" href="/observatory/requests/new">New request</a><a className="underline" href="/observatory/executions/new">Run once</a></div></div>
    <p className="text-muted-foreground">Save what to crawl: sources, selection, scope and budgets. Run it now or attach a schedule. Edits affect future executions only.</p>
    {definitions.isPending && <p role="status">Loading requests…</p>}
    {definitions.error && <p role="alert">{extractApiError(definitions.error)}</p>}
    {id && <a className="block underline" href="/observatory/requests">All requests</a>}
    {id && definitions.data && !request && <p>This request is not in the loaded saved requests.</p>}
    {(id ? request ? [request] : [] : definitions.data)?.map((item) => <Card key={item.id}>
      <CardHeader><CardTitle><a className="underline" href={`/observatory/requests/${item.id}`}>{item.name}</a></CardTitle><CardDescription>Version {item.version} · {item.specification.request_class} · {item.specification.page_limit} pages · Depth {item.specification.max_depth} · Maximum duration {item.specification.max_duration_seconds ? `${item.specification.max_duration_seconds} seconds` : "unlimited"}</CardDescription></CardHeader>
      <CardContent className="space-y-4"><p className="break-all">{item.specification.seed_description || item.specification.seed_urls.join(", ") || item.specification.seed_sql}</p>
        <div className="flex flex-wrap gap-3"><Button disabled={action.isPending} onClick={() => action.mutate({path: `/request-definitions/${item.id}/run`}, {onSuccess: (result) => {if(result.request_id) window.location.assign(`/observatory/executions/${result.request_id}`)}})}>Run now</Button>
        {id ? <Button variant="outline" onClick={() => setEditing(true)}>Edit request</Button> : <a className="self-center underline" href={`/observatory/requests/${item.id}`}>Open request</a>}
        <a className="self-center underline" href={`/observatory/schedules?request=${item.id}`}>Add schedule</a></div>
        {id && <div className="space-y-3">
          <p>Priority {item.priority} · Reuse results up to {item.specification.result_max_age_seconds} seconds old · Retention {item.specification.retention_seconds ? `${item.specification.retention_seconds} seconds after completion` : "forever"}</p>
          <p>Allowed sections: {item.specification.allowed_sections.join(", ") || "Unrestricted"}</p>
          <details><summary>Starting URLs</summary><ul>{item.specification.seed_urls.map((url) => <li key={url} className="break-all">{url}</li>)}</ul></details>
          {item.specification.seed_sql && <details><summary>Seed SQL</summary><pre className="overflow-auto text-sm">{item.specification.seed_sql}</pre><p>Parameters: {JSON.stringify(item.specification.seed_parameters)}</p></details>}
          <details><summary>Follow-link SQL</summary><pre className="overflow-auto text-sm">{item.specification.follow_sql}</pre></details>
        </div>}
      </CardContent>
    </Card>)}
    {definitions.data?.length === 0 && <p>No saved requests yet. Create one to reuse it, or choose Run once for a single execution.</p>}
    {request && <><h2 className="text-xl font-semibold">Schedules</h2>
      {schedules.error && <p role="alert">{extractApiError(schedules.error)}</p>}
      {schedules.isPending && <p>Loading schedules…</p>}
      {schedules.data?.filter((item) => item.definition_id === request.id).map((item) => <Card key={item.id}><CardContent className="space-y-3 pt-4"><ScheduleSummary schedule={item}/><a className="block underline" href={`/observatory/schedules/${item.id}`}>Manage timing</a></CardContent></Card>)}
      {schedules.data && !schedules.data.some((item) => item.definition_id === request.id) && <p>No schedules in the loaded set.</p>}
      <RecentExecutions requestId={request.id}/>
    </>}
    <p className="text-xs text-muted-foreground">Showing up to 200 saved requests and schedules.</p>
  </div>
}
function RecentExecutions({ requestId }: { requestId: string }) {
  const query = useCollections(0, "")
  const items = query.data?.items.filter((item) => item.specification.origin?.definition_id === requestId)
  return <section className="space-y-3"><h2 className="text-xl font-semibold">Recent executions</h2>
    <p className="text-sm text-muted-foreground">Matches in the latest 20 retained executions. Older and retired runs are available in Executions.</p>
    {query.isPending && <p>Loading executions…</p>}{query.error && <p role="alert">{extractApiError(query.error)}</p>}
    {items?.map((item) => <p key={item.id}><a className="underline" href={`/observatory/executions/${item.id}`}>{new Date(item.created_at).toLocaleString()}</a> · {item.status} · {item.outcome?.replaceAll("_", " ") ?? "In progress"}</p>)}
    {items?.length === 0 && <p>No matching executions in this window.</p>}<a className="block underline" href="/observatory/executions">All executions</a>
  </section>
}
