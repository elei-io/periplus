import { useState } from "react"
import { useCollectionHistory, useCollections } from "@/hooks/use-collections"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { extractApiError } from "@/lib/api"

export function CollectionsPage() {
  const [tab, setTab] = useState<"current" | "history">("current")
  return (
    <div className="flex w-full flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Collections</h1>
        <a className="underline" href="/collections/new">
          New collection
        </a>
      </div>
      <p className="text-muted-foreground">
        Requests guide the shared crawler. Settlement and query readiness are
        tracked separately.
      </p>
      <div className="flex gap-2" aria-label="Collection source">
        <Button
          variant={tab === "current" ? "default" : "outline"}
          onClick={() => setTab("current")}
        >
          Current requests
        </Button>
        <Button
          variant={tab === "history" ? "default" : "outline"}
          onClick={() => setTab("history")}
        >
          Durable history
        </Button>
      </div>
      {tab === "current" ? <CurrentRequests /> : <History />}
    </div>
  )
}
function CurrentRequests() {
  const [offset, setOffset] = useState(0)
  const [status, setStatus] = useState("")
  const query = useCollections(offset, status)
  return (
    <>
      <div className="flex flex-wrap gap-2" aria-label="Filter by status">
        {["", "active", "paused", "settled"].map((value) => (
          <Button
            key={value}
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
      {query.isPending && <p role="status">Loading requests…</p>}
      {query.isError && (
        <p role="alert">
          {query.data ? "Status may be stale. " : "Unable to load requests. "}
          {extractApiError(query.error)}
        </p>
      )}
      {query.data?.items.map((item) => (
        <Card key={item.id}>
          <CardHeader>
            <CardTitle>
              <a
                className="break-all underline"
                href={`/collections/${item.id}`}
              >
                {item.specification.seed_description ||
                  item.specification.seed_urls[0] ||
                  "SQL collection"}
              </a>
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            <div className="flex flex-wrap gap-2">
              <Badge>{item.status}</Badge>
              <Badge variant="outline">{item.specification.visibility}</Badge>
              <span>{item.outcome?.replaceAll("_", " ")}</span>
            </div>
            <p>
              {item.supplied_pages} supplied · {item.failed_pages} failed ·{" "}
              {item.consumed_pages} of {item.specification.page_limit} page
              units consumed · {item.reserved_pages} reserved
            </p>
            <p className="text-sm text-muted-foreground">
              As of {new Date(item.as_of).toLocaleString()} · {item.id}
            </p>
          </CardContent>
        </Card>
      ))}
      {query.data?.items.length === 0 && (
        <p>
          No current requests on this page. Retired requests are available in
          durable history.
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
        Immutable collection records. A definition can arrive before its
        outcome; missing counts remain unknown. Restart from the newest page to
        see newly ingested records.
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
                href={`/collections/${item.id}`}
              >
                {item.summary || item.id}
              </a>
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            <div>
              <Badge variant="outline">{item.visibility}</Badge>{" "}
              {item.outcome?.replaceAll("_", " ") || "Outcome not yet recorded"}
            </div>
            <p>
              {item.supplied_pages ?? "Unknown"} supplied ·{" "}
              {item.failed_pages ?? "Unknown"} failed
            </p>
            <p className="text-sm text-muted-foreground">
              Requested {new Date(item.created_at).toLocaleString()}
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
