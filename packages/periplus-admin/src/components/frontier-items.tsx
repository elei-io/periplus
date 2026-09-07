"use client"
import { StartWindow } from "@/components/start-window"
import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { extractApiError } from "@/lib/api"
import type {
  AcquisitionView,
  ObservationLineagePage,
  CollectionItemsPage,
  CollectionArrivalsPage,
} from "@/types/frontier-items"

class FrontierReadError extends Error {
  readonly status: number
  constructor(status: number, message: string) { super(message); this.status = status }
}

async function read<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(`/api${path}`, {
    signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]),
    cache: "no-store",
  })
  const body = await response.json()
  if (!response.ok)
    throw new FrontierReadError(
      response.status, typeof body?.detail === "string" ? body.detail : "Frontier read failed."
    )
  return body as T
}
const collectionLink = (id: string) => `/collections/${encodeURIComponent(id)}`
const itemLink = (id: string) => `/frontier/items/${encodeURIComponent(id)}`
function modeLabel(mode: string) {
  return mode === "acquired"
    ? "Acquisition requested"
    : mode === "shared"
      ? "Shared work"
      : mode === "reused"
        ? "Recent result reuse"
        : mode
}
function observationLink(id: string) {
  if (
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)
  )
    return null
  return `/sql?${new URLSearchParams({ sql: `SELECT * FROM web.observation WHERE observation_id = '${id}' LIMIT 1;` })}`
}
function Observation({ id, label }: { id: string; label: string }) {
  const href = observationLink(id)
  return href ? (
    <a className="break-all underline" href={href}>
      {label}: {id}
    </a>
  ) : null
}
function Readiness({ item }: { item: Pick<AcquisitionView, "query_ready" | "query_readiness_reason"> }) {
  return <p>{item.query_ready === true ? "Query readiness verified for the active generation."
    : item.query_ready === false ? "Materialization is still pending."
    : item.query_readiness_reason === "catalogue_readiness_unavailable" ? "Readiness check unavailable; showing current frontier state."
    : "Query readiness has not been verified."}</p>
}

export function CollectionItems({ id }: { id: string }) {
  const [cursors, setCursors] = useState<Array<string | null>>([null])
  const cursor = cursors[cursors.length - 1]
  const query = useQuery({
    queryKey: ["collection-items", id, cursor],
    queryFn: ({ signal }) =>
      read<CollectionItemsPage>(
        `/collections/${encodeURIComponent(id)}/items?limit=10${cursor ? `&after=${encodeURIComponent(cursor)}` : ""}`,
        signal
      ),
    refetchInterval: 10000,
    retry: false,
  })
  return (
    <section className="flex min-w-0 flex-col gap-4">
      <h2>Request frontier items</h2>
      <p className="text-sm text-muted-foreground">
        Current retained work, ordered by identity, not dispatch position. Newly
        admitted items may appear on earlier pages; refresh from the beginning
        to review them.
      </p>
      <Button
        variant="outline"
        className="self-start"
        disabled={query.isFetching}
        onClick={() => {
          setCursors([null])
          if (cursors.length === 1) void query.refetch()
        }}
      >
        Refresh first items
      </Button>
      {query.isPending && <p role="status">Loading frontier items…</p>}
      {query.error && (
        <p role="alert">
          {query.data
            ? "Frontier items may be stale. "
            : "Current frontier items unavailable. "}
          {extractApiError(query.error)}
        </p>
      )}
      {query.data && (
        <p className="text-sm text-muted-foreground">
          As of {new Date(query.data.as_of).toLocaleString()}
        </p>
      )}
      {query.data?.items.length === 0 && (
        <p>
          No admitted items on this page. Starting URL selection and admission
          may still be in progress.
        </p>
      )}
      {query.data?.items.map((item) => (
        <Card key={item.interest_id}>
          <CardHeader>
            <CardTitle>
              <a
                className="break-all underline"
                href={itemLink(item.acquisition.id)}
              >
                {item.acquisition.url}
              </a>
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            <p>
              Request work: {item.status.replaceAll("_", " ")} ·{" "}
              {modeLabel(item.mode)} · Page unit {item.budget_state}
            </p>
            <p>
              Depth {item.context.depth} · Rule {item.context.rule_id} ·
              Acquisition {item.acquisition.status}
            </p>
            {item.acquisition.waiting_reason && (
              <p>{item.acquisition.waiting_reason.replaceAll("_", " ")}</p>
            )}
            {item.context.parent_observation_id && (
              <Observation
                id={item.context.parent_observation_id}
                label="Parent observation"
              />
            )}
            {item.acquisition.observation_id && (
              <Observation
                id={item.acquisition.observation_id}
                label="Observation"
              />
            )}
            {item.acquisition.observation_id && <Readiness item={item.acquisition} />}
            <p className="text-sm text-muted-foreground">
              Admitted {new Date(item.admitted_at).toLocaleString()}
            </p>
          </CardContent>
        </Card>
      ))}
      <div className="flex gap-2">
        <Button
          variant="outline"
          disabled={cursors.length === 1 || query.isFetching}
          onClick={() => setCursors(cursors.slice(0, -1))}
        >
          Previous items
        </Button>
        <Button
          variant="outline"
          disabled={
            query.isError || query.isFetching || !query.data?.next_after
          }
          onClick={() => {
            if (query.data?.next_after)
              setCursors([...cursors, query.data.next_after])
          }}
        >
          Next items
        </Button>
      </div>
    </section>
  )
}
export function FrontierItemDetail({ id }: { id: string }) {
  const query = useQuery({
    queryKey: ["frontier-item", id],
    queryFn: ({ signal }) =>
      read<AcquisitionView>(
        `/frontier/items/${encodeURIComponent(id)}`,
        signal
      ),
    refetchInterval: 10000,
    retry: false,
  })
  const missing = query.error instanceof FrontierReadError && query.error.status === 404
  const item = missing ? undefined : query.data
  return (
    <div className="flex w-full min-w-0 flex-col gap-4">
      <h1 className="text-2xl font-semibold">Frontier item</h1>
      <Button
        className="self-start"
        variant="outline"
        disabled={query.isFetching}
        onClick={() => void query.refetch()}
      >
        Refresh frontier item
      </Button>
      {query.isPending && <p role="status">Loading frontier item…</p>}
      {missing && <p>No current frontier record is available. Committed provenance is shown below when present.</p>}
    {query.error && !missing && (
        <p role="alert">
          {item
            ? "Frontier status may be stale. "
            : "Current frontier item unavailable. "}
          {extractApiError(query.error)}
        </p>
      )}
      {item && (
        <>
          <p className="break-all">{item.url}</p>
          <div>
            <Badge>{item.status}</Badge>
          </div>
          <p>
            {item.domain} · {item.attempt_count} physical attempts started
          </p>
          <p className="text-sm text-muted-foreground">
            As of {new Date(item.as_of).toLocaleString()} · Created{" "}
            {new Date(item.created_at).toLocaleString()}
          </p>
          {item.waiting_reason && (
            <p>Current stage: {item.waiting_reason.replaceAll("_", " ")}</p>
          )}
          {item.eligibility_not_before && (
            <p>
              Eligibility floor:{" "}
              {new Date(item.eligibility_not_before).toLocaleString()}. This is
              not a promised start time; domain and dispatch constraints still
              apply.
            </p>
          )}
      {item.terminal_reason && <p>Acquisition stopped: {item.terminal_reason.replaceAll("_", " ")}.</p>}
          {item.next_start_estimate ? <StartWindow estimate={item.next_start_estimate} /> : <p>
            Next-start estimate unavailable:{" "}
            {item.estimate_unavailable_reason?.replaceAll("_", " ")}.
          </p>}
          <p>
            Evidence commit:{" "}
            {item.evidence_committed ? "confirmed" : "not yet confirmed"}.
          </p>
          <Readiness item={item} />
          {item.observation_id && (
            <Observation id={item.observation_id} label="Query observation" />
          )}
          <Card>
            <CardHeader>
              <CardTitle>Requesting collections</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              <p className="text-sm text-muted-foreground">
                Visible current associations. Retired collection lineage remains
                in the catalogue.
              </p>
              {item.callers.map((caller) => (
                <div key={caller.interest_id}>
                  <a
                    className="break-all underline"
                    href={collectionLink(caller.collection_id)}
                  >
                    {caller.collection_id}
                  </a>
                  <p>
                    {modeLabel(caller.mode)} ·{" "}
                    {caller.status.replaceAll("_", " ")}
                  </p>
                </div>
              ))}
              {item.more_callers && (
                <p>More visible associations exist; this preview shows ten.</p>
              )}
              {!item.callers.length && (
                <p>No visible current collection associations.</p>
              )}
            </CardContent>
          </Card>
          {item.background && (
            <Card>
              <CardHeader>
                <CardTitle>Background exploration</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-col gap-2">
                <p>
                  Selection rule: {item.background_rule_id || "Not recorded"}
                </p>
                {item.background_parent_observation_id && (
                  <Observation
                    id={item.background_parent_observation_id}
                    label="Background parent observation"
                  />
                )}
              </CardContent>
            </Card>
          )}
        </>
      )}
    <ObservationLineage key={item?.observation_id ?? id} id={item?.observation_id ?? id} />
</div>
  )
}

export function CollectionArrivals({ id }: { id: string }) {
  const [cursors, setCursors] = useState<Array<string | null>>([null])
  const cursor = cursors[cursors.length - 1]
  const query = useQuery({
    queryKey: ["collection-arrivals", id, cursor],
    queryFn: ({ signal }) =>
      read<CollectionArrivalsPage>(
        `/collections/${encodeURIComponent(id)}/arrivals?limit=10${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
        signal
      ),
    refetchInterval: 10000,
    retry: false,
  })
  return (
    <section className="flex min-w-0 flex-col gap-4">
      <h2>Durable arrivals</h2>
      <p className="text-sm text-muted-foreground">
        Fulfillments recorded in the catalogue, newest decisions first. They
        remain available after current frontier work is retired. Refresh from
        the newest page to include later commits.
      </p>
      <Button
        variant="outline"
        className="self-start"
        disabled={query.isFetching}
        onClick={() => {
          setCursors([null])
          if (cursors.length === 1) void query.refetch()
        }}
      >
        Refresh newest arrivals
      </Button>
      {query.isPending && <p role="status">Loading arrivals…</p>}
      {query.error && (
        <p role="alert">
          {query.data ? "Arrivals may be stale. " : "Arrivals unavailable. "}
          {extractApiError(query.error)}
        </p>
      )}
      {query.data && (
        <p className="text-sm text-muted-foreground">
          As of {new Date(query.data.as_of).toLocaleString()}
        </p>
      )}
      {query.data && !query.data.definition_committed && (
        <p>Collection definition is awaiting its catalogue commit.</p>
      )}
      {query.data?.definition_committed && query.data.items.length === 0 && (
        <p>
          No durable arrivals on this page. Ingestion may still be in progress.
        </p>
      )}
      {query.data?.items.map((item) => (
        <Card key={item.fulfillment_id}>
          <CardHeader>
            <CardTitle>
              <span className="break-all">{item.requested_url}</span>
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            <p>
              {item.mode === "acquired"
                ? "Acquired result"
                : item.mode === "shared"
                  ? "Shared result"
                  : "Reused result"}{" "}
              · Depth {item.depth} · Rule {item.rule_id}
            </p>
            <p>
              Fulfillment recorded {new Date(item.decided_at).toLocaleString()}
            </p>
            <p>
              {item.observation_committed
                ? `Observation committed: ${item.outcome ?? "outcome unavailable"}${item.http_status_code === null ? "" : ` · HTTP ${item.http_status_code}`}`
                : "Observation commit not yet confirmed."}
            </p>
            {item.effective_url && (
              <p className="break-all">Effective URL: {item.effective_url}</p>
            )}
            <p>{item.query_ready === true ? "Query readiness verified for the active generation." : item.query_ready === false ? "Materialization is still pending." : "Query readiness has not been verified."}</p>
            <Observation id={item.observation_id} label="Query observation" />
            {item.parent_observation_id && (
              <Observation
                id={item.parent_observation_id}
                label="Traversal parent"
              />
            )}
          </CardContent>
        </Card>
      ))}
      <div className="flex gap-2">
        <Button
          variant="outline"
          disabled={cursors.length === 1 || query.isFetching}
          onClick={() => setCursors(cursors.slice(0, -1))}
        >
          Previous arrivals
        </Button>
        <Button
          variant="outline"
          disabled={
            query.isError || query.isFetching || !query.data?.next_cursor
          }
          onClick={() => {
            if (query.data?.next_cursor)
              setCursors([...cursors, query.data.next_cursor])
          }}
        >
          Next arrivals
        </Button>
      </div>
    </section>
  )
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
    <h2>Durable provenance</h2>
    <p>Capture causes explain why acquisition started. Result uses include requests that shared or later reused the observation. These records survive frontier cleanup; ingestion may still add records. Refresh the first page to see later commits.</p>
    <Button variant="outline" className="self-start" disabled={query.isFetching} onClick={() => { setCursors([null]); if (cursors.length === 1) void query.refetch() }}>Refresh newest provenance</Button>
    {query.isPending && <p role="status">Loading provenance…</p>}
    {missing && <p>No visible committed observation is available yet.</p>}
    {query.error && !missing && <p role="alert">{query.data ? "Provenance may be stale. " : "Provenance unavailable. "}{extractApiError(query.error)}</p>}
    {query.data && <><p className="break-all">{query.data.requested_url}</p><p className="text-sm text-muted-foreground">As of {new Date(query.data.as_of).toLocaleString()}</p><Observation id={query.data.observation_id} label="Query observation" /></>}
    {query.data?.items.length === 0 && <p>No visible provenance records on this page. Imported observations may have no collection or capture cause.</p>}
    {query.data?.items.map(item => <Card key={`${item.kind}:${item.record_id}`}><CardHeader><CardTitle>{item.kind === "reason" ? "Capture cause" : "Result use"}</CardTitle></CardHeader><CardContent className="flex flex-col gap-2">
      <p>{item.kind === "reason" ? item.reason === "background" ? "Background exploration" : "Collection request" : item.mode === "reused" ? "Reused result" : item.mode === "shared" ? "Shared result" : "Acquired result"}</p>
      {item.collection_id && <a className="break-all underline" href={collectionLink(item.collection_id)}>Collection: {item.collection_id}</a>}
      <p>Rule {item.rule_id}{item.depth !== null ? ` · Depth ${item.depth}` : ""}{item.policy_version !== null ? ` · Policy ${item.policy_version}` : ""}</p>
      {item.parent_observation_id && <a className="break-all underline" href={itemLink(item.parent_observation_id)}>Parent observation: {item.parent_observation_id}</a>}
      <p>Recorded {new Date(item.decided_at).toLocaleString()}</p>
    </CardContent></Card>)}
    <div className="flex gap-2"><Button variant="outline" disabled={cursors.length === 1 || query.isFetching} onClick={() => setCursors(cursors.slice(0, -1))}>Previous provenance</Button><Button variant="outline" disabled={query.isError || query.isFetching || !query.data?.next_cursor} onClick={() => { if (query.data?.next_cursor) setCursors([...cursors, query.data.next_cursor]) }}>Next provenance</Button></div>
  </section>
}
