import { useState } from "react"
import { useCollectionHistory, useCollections } from "@/hooks/use-collections"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card"
import type { CurrentCollection } from "@/types/collections"
import { extractApiError } from "@/lib/api"

export function CollectionsPage() {
  const [tab, setTab] = useState<"current" | "history">("current")
  return (
    <div className="flex w-full flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Executions</h1>
        <a className="underline" href="/observatory/executions/new">
          Run once
        </a>
      </div>
      <a
        className="self-start underline"
        href="/observatory/schedules"
      >
        Schedules
      </a>
      <p className="text-muted-foreground">
        Inspect crawl intent, progress and the constraints holding work back.
        Execution settlement and query readiness are separate.
      </p>
      <div className="flex gap-2" aria-label="Execution source">
        <Button
          aria-pressed={tab === "current"}
          variant={tab === "current" ? "default" : "outline"}
          onClick={() => setTab("current")}
        >
          Current executions
        </Button>
        <Button
          aria-pressed={tab === "history"}
          variant={tab === "history" ? "default" : "outline"}
          onClick={() => setTab("history")}
        >
          Completed & history
        </Button>
      </div>
      {tab === "current" ? <CurrentRequests /> : <History />}
    </div>
  )
}
function CurrentRequests() {
  const [offset, setOffset] = useState(0)
  const [status, setStatus] = useState("active")
  const query = useCollections(offset, status)
  return (
    <>
      <div className="flex flex-wrap gap-2" aria-label="Filter by status">
        {["", "active", "paused", "settled"].map((value) => (
          <Button
            key={value}
            aria-pressed={status === value}
            className="capitalize"
            variant={status === value ? "secondary" : "outline"}
            onClick={() => {
              setStatus(value)
              setOffset(0)
            }}
          >
            {value || "All statuses"}
          </Button>
        ))}
        <Button
          variant="outline"
          disabled={query.isFetching}
          onClick={() => void query.refetch()}
        >
          Refresh
        </Button>
      </div>
      {query.isPending && <p role="status">Loading executions…</p>}
      {query.isError && (
        <p role="alert">
          {query.data ? "Status may be stale. " : "Unable to load executions. "}
          {extractApiError(query.error)}
        </p>
      )}
      {query.data && (
        <>
          <div className="grid gap-3 sm:grid-cols-3">
            <Summary
              label="Executions on this page"
              value={query.data.items.length}
            />
            <Summary
              label="Acquiring pages"
              value={query.data.items.reduce(
                (sum, item) => sum + item.acquiring_pages,
                0
              )}
            />
            <Summary
              label="Deferred pages"
              value={query.data.items.reduce(
                (sum, item) => sum + item.queue.deferred_pages,
                0
              )}
            />
          </div>
          <p className="text-xs text-muted-foreground">
            Counts cover this page of retained executions, not the whole crawler.
            Refreshes every 5 seconds. An execution can share acquisitions with
            other executions.
          </p>
        </>
      )}
      {query.data?.items.map((item) => (
        <RequestCard key={item.id} item={item} />
      ))}
      {query.data?.items.length === 0 && (
        <p>
          No {status || "current"} executions on this page. Change the status
          filter or open history to inspect other executions.
        </p>
      )}
      <div className="flex items-center gap-3">
        <Button
          variant="outline"
          disabled={offset === 0 || query.isFetching}
          onClick={() => setOffset(Math.max(0, offset - 20))}
        >
          Previous
        </Button>
        <span>Page {offset / 20 + 1}</span>
        <Button
          variant="outline"
          disabled={
            query.isError ||
            query.isFetching ||
            query.data?.items.length !== 20 ||
            offset >= 10000
          }
          onClick={() => setOffset(offset + 20)}
        >
          Next
        </Button>
      </div>
    </>
  )
}
function History() {
  const [cursors, setCursors] = useState<Array<string | null>>([null])
  const query = useCollectionHistory(cursors.at(-1) ?? null)
  return (
    <>
      <p className="text-sm text-muted-foreground">
        Immutable execution records. Frozen intent can arrive before its outcome;
        missing counts remain unknown. Restart from the newest page to see newly
        ingested records.
      </p>
      <Button
        className="self-start"
        variant="outline"
        disabled={query.isFetching}
        onClick={() => {
          setCursors([null])
          if (cursors.length === 1) void query.refetch()
        }}
      >
        Refresh newest history
      </Button>
      {query.isPending && <p role="status">Loading history…</p>}
      {query.isError && (
        <p role="alert">
          History is unavailable. {extractApiError(query.error)}
        </p>
      )}
      {query.data && (
        <p className="text-sm text-muted-foreground">
          As of {new Date(query.data.as_of).toLocaleString()}
        </p>
      )}
      {query.data?.items.map((item) => (
        <Card key={item.id}>
          <CardHeader>
            <CardTitle>
              <a
                className="break-all underline"
                href={`/observatory/executions/${item.id}`}
              >
                {item.summary || item.id}
              </a>
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            <div>
              <Badge variant="outline">{item.request_class}</Badge>{" "}
              {item.outcome?.replaceAll("_", " ") || "Outcome not yet recorded"}
            </div>
            <p>
              {item.supplied_pages ?? "Unknown"} supplied ·{" "}
              {item.failed_pages ?? "Unknown"} failed
            </p>
            <p className="text-sm text-muted-foreground">
              Started {new Date(item.created_at).toLocaleString()} ·{" "}
              {item.completed_at
                ? `Completed ${new Date(item.completed_at).toLocaleString()}`
                : "Completion not recorded"}
            </p>
          </CardContent>
        </Card>
      ))}
      {query.data?.items.length === 0 && (
        <p>No durable records on this page.</p>
      )}
      <div className="flex gap-3">
        <Button
          variant="outline"
          disabled={cursors.length === 1 || query.isFetching}
          onClick={() => setCursors(cursors.slice(0, -1))}
        >
          Previous
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
          Next
        </Button>
      </div>
    </>
  )
}

function Summary({ label, value }: { label: string; value: number }) {
  return (
    <Card>
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-2xl tabular-nums">
          {value.toLocaleString()}
        </CardTitle>
      </CardHeader>
    </Card>
  )
}

function RequestCard({ item }: { item: CurrentCollection }) {
  const spec = item.specification
  const title = spec.seed_description || spec.seed_urls[0] || "SQL execution"
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="min-w-0 text-base">
            <a
              className="break-all underline underline-offset-4"
              href={`/observatory/executions/${item.id}`}
            >
              {title}
            </a>
          </CardTitle>
          <div className="flex gap-2">
            <Badge variant={item.status === "paused" ? "outline" : "secondary"}>
              {item.status}
            </Badge>
            <Badge variant="outline">{spec.request_class}</Badge>
          </div>
        </div>
        <CardDescription>
          {spec.seed_description
            ? "Description discovery"
            : spec.seed_sql
              ? "SQL selection"
              : `${spec.seed_urls.length} starting URLs`}{" "}
          · Depth {spec.max_depth} · Priority {item.priority}
          {item.outcome ? ` · ${item.outcome.replaceAll("_", " ")}` : ""}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <Value
            label="Supplied / failed"
            value={`${item.supplied_pages} / ${item.failed_pages}`}
          />
          <Value
            label="Consumed / page limit"
            value={`${item.consumed_pages} / ${spec.page_limit}`}
          />
          <Value
            label="Acquiring / selecting"
            value={`${item.acquiring_pages} / ${item.selecting_pages}`}
          />
          <Value
            label="Reserved page units"
            value={String(item.reserved_pages)}
          />
        </div>
        {item.status !== "settled" && (
          <div className="rounded-md border bg-muted/20 p-3 text-sm">
            <p>
              {item.queue.runnable_pages} runnable · {item.queue.deferred_pages}{" "}
              deferred · {item.queue.unknown_pages} eligibility unknown
            </p>
            {item.waiting_reason && (
              <p className="mt-2 font-medium">
                Waiting: {item.waiting_reason.replaceAll("_", " ")}
              </p>
            )}
            {item.queue.constraints.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-2">
                {item.queue.constraints.map((constraint) => (
                  <Badge key={constraint.reason} variant="outline">
                    {constraint.reason.replaceAll("_", " ")}: {constraint.pages}
                  </Badge>
                ))}
              </div>
            )}
            {!item.seeds_settled && (
              <p className="mt-2 text-xs text-muted-foreground">
                Starting URL selection or admission is still in progress.{" "}
                {item.admission.pending_candidates} frozen candidates await
                admission.
              </p>
            )}
            {item.queue.oldest_wait_seconds !== null && (
              <p className="mt-2 text-xs text-muted-foreground">
                Oldest queued page:{" "}
                {Math.floor(item.queue.oldest_wait_seconds / 60)} minutes
                waiting. Runnable work still requires capacity at dispatch.
              </p>
            )}
          </div>
        )}
        <div className="flex flex-wrap justify-between gap-2 text-xs text-muted-foreground">
          <span>
            Last progress:{" "}
            {item.last_progress_at
              ? new Date(item.last_progress_at).toLocaleString()
              : "Not recorded"}
          </span>
          <span>Submitted {new Date(item.created_at).toLocaleString()}</span>
        </div>
        <p className="text-xs text-muted-foreground">
          Observed {new Date(item.as_of).toLocaleString()} ·{" "}
          <span className="font-mono break-all">{item.id}</span>
        </p>
      </CardContent>
    </Card>
  )
}
function Value({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xl font-medium tabular-nums">{value}</p>
      <p className="mt-1 text-xs text-muted-foreground">{label}</p>
    </div>
  )
}
