import { CollectionQueueSummary } from "@/components/collection-queue"
import { AdmissionWaitSummary } from "@/components/admission-wait"
import { useState } from "react"
import {
  CollectionItems,
  CollectionArrivals,
} from "@/components/frontier-items"
import { toast } from "sonner"
import { useChangeCollection, useCollection } from "@/hooks/use-collections"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { extractApiError } from "@/lib/api"
import type { CurrentCollection } from "@/types/collections"

export function CollectionDetailPage({ id }: { id: string }) {
  const query = useCollection(id)
  const item = query.data
  return (
    <div className="flex w-full min-w-0 flex-col gap-5">
      <a className="underline" href="/observatory/executions">
        All executions
      </a>
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">Execution</h1>
        <Button
          variant="outline"
          disabled={query.isFetching}
          onClick={() => void query.refetch()}
        >
          Refresh execution
        </Button>
      </div>
      {query.isPending && <p role="status">Loading execution…</p>}
      {query.isError && (
        <p role="alert">
          {item ? "Status may be stale. " : "Unable to load execution. "}
          {extractApiError(query.error)}
        </p>
      )}
      {item && (
        <>
          <p className="text-sm break-all text-muted-foreground">
            {item.id} · As of {new Date(item.as_of).toLocaleString()}
          </p>
          {item.specification.origin && (
            <p className="text-sm">
              From{" "}
              <a className="underline" href={`/observatory/requests/${item.specification.origin.definition_id}`}>
                saved request
              </a>{" "}
              v{item.specification.origin.definition_version} ·{" "}
              {item.specification.origin.schedule_id
                ? <a className="underline" href={`/observatory/schedules/${item.specification.origin.schedule_id}`}>Schedule</a>
                : "Manual execution"}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <Badge>
              {item.source === "current" ? item.status : "Durable history"}
            </Badge>
            <Badge variant="outline">{item.specification.request_class}</Badge>
            {item.outcome && (
              <Badge variant="secondary">
                {item.outcome.replaceAll("_", " ")}
              </Badge>
            )}
          </div>
          {item.source === "history" && (
            <p>
              Current execution has retired. This immutable record may precede
              its outcome; missing counts remain unknown.
            </p>
          )}
          {item.source === "current" && (
            <>
              <p>
                {item.seeds_settled
                  ? "Starting URL selection has finished."
                  : "Starting URLs are still being selected or admitted; counts can grow."}{" "}
                {item.discovery_stage &&
                  `Source discovery: ${item.discovery_stage}.`}
              </p>
              {item.waiting_reason && (
                <p role="status">
                  Waiting: {item.waiting_reason.replaceAll("_", " ")}
                </p>
              )}
              <CollectionQueueSummary item={item} />
              <AdmissionWaitSummary value={item.admission} />
              {item.status !== "settled" && (
                <CollectionControls
                  key={item.id}
                  item={item}
                  unavailable={query.isError}
                />
              )}
            </>
          )}
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <Metric
              title="Pages supplied"
              value={item.supplied_pages}
              description="Results that passed capture checks. Usefulness and completeness remain unverified."
            />
            <Metric
              title="Failed pages"
              value={item.failed_pages}
              description="Terminal acquisition failures attributed to this execution."
            />
            <Metric
              title="Page budget consumed"
              value={item.consumed_pages}
              description={`Limit ${item.specification.page_limit}. Retries do not consume another page unit.`}
            />
            {item.source === "current" && (
              <>
                <Metric
                  title="Page units reserved"
                  value={item.reserved_pages}
                  description="Admitted work awaiting dispatch or reuse."
                />
                <Metric
                  title="Queued / acquiring / selecting"
                  value={`${item.queued_pages} / ${item.acquiring_pages} / ${item.selecting_pages}`}
                  description="Current execution work, not a completion percentage."
                />
                <Metric
                  title="Shared / reused"
                  value={`${item.shared_pages} / ${item.reused_pages}`}
                  description="Execution associations with shared work or recent results; not physical browser attempts."
                />
              </>
            )}
          </div>
          <Card>
            <CardHeader>
              <CardTitle>Catalogue readiness</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-2">
              <p>
                {item.query_ready === true
                  ? "Query readiness verified for the complete collection."
                  : item.query_ready === false
                    ? "Not yet query-ready."
                    : "Query readiness has not been verified."}
              </p>
              <p className="text-sm text-muted-foreground">
                {item.query_readiness_reason.replaceAll("_", " ")}. Execution
                settlement alone does not prove catalogue or materialization
                completion.
              </p>
              {item.query_readiness_as_of && (
                <p>
                  Checked{" "}
                  {new Date(item.query_readiness_as_of).toLocaleString()}
                </p>
              )}
              {item.source === "current" && (
                <p>
                  {item.ingested_pages} page evidence commits confirmed.
                  Collection lineage:{" "}
                  {item.lineage_ready ? "confirmed" : "not yet confirmed"}.
                </p>
              )}
            </CardContent>
          </Card>
          {item.source === "current" && (
            <CollectionItems key={item.id} id={item.id} />
          )}
          <CollectionArrivals key={`arrivals-${item.id}`} id={item.id} />
          <Card>
            <CardHeader>
              <CardTitle>Frozen intent</CardTitle>
            </CardHeader>
            <CardContent className="flex min-w-0 flex-col gap-3">
              {item.specification.seed_description && (
                <p className="break-words whitespace-pre-wrap">
                  {item.specification.seed_description}
                </p>
              )}
              <p>
                Maximum depth {item.specification.max_depth} · Recent-result
                reuse up to {item.specification.result_max_age_seconds} seconds
                · Class: {item.specification.request_class}
              </p>
              <p>
                Started {new Date(item.created_at).toLocaleString()}
                {item.completed_at &&
                  ` · Settled ${new Date(item.completed_at).toLocaleString()}`}
              </p>
              <p>
                Maximum duration:{" "}
                {item.specification.max_duration_seconds
                  ? `${item.specification.max_duration_seconds} seconds · Deadline ${new Date(item.specification.deadline_at!).toLocaleString()}`
                  : "None"}
              </p>
              <details>
                <summary>
                  Starting URLs ({item.specification.seed_urls.length})
                </summary>
                <pre className="overflow-auto text-sm break-all whitespace-pre-wrap">
                  {item.specification.seed_urls.join("\n") ||
                    "No explicit URLs"}
                </pre>
              </details>
              <details>
                <summary>
                  Allowed sections ({item.specification.allowed_sections.length}
                  )
                </summary>
                <pre className="overflow-auto text-sm break-all whitespace-pre-wrap">
                  {item.specification.allowed_sections.join("\n") ||
                    "No execution-specific section restriction"}
                </pre>
              </details>
              {item.specification.seed_sql && (
                <details>
                  <summary>Seed SQL and parameters</summary>
                  <pre className="overflow-auto text-sm break-all whitespace-pre-wrap">
                    {item.specification.seed_sql}
                    {"\n"}
                    {JSON.stringify(
                      item.specification.seed_parameters,
                      null,
                      2
                    )}
                  </pre>
                </details>
              )}
              <details>
                <summary>Follow-link SQL</summary>
                <pre className="overflow-auto text-sm break-all whitespace-pre-wrap">
                  {item.specification.follow_sql}
                </pre>
              </details>
              {item.source === "current" && item.search_queries.length > 0 && (
                <details>
                  <summary>Discovery searches and resolved URLs</summary>
                  <pre className="overflow-auto text-sm break-all whitespace-pre-wrap">
                    {[...item.search_queries, ...item.resolved_urls].join("\n")}
                  </pre>
                </details>
              )}
              {item.source === "history" && item.seed_provenance && (
                <details>
                  <summary>Recorded seed provenance</summary>
                  <pre className="overflow-auto text-sm break-all whitespace-pre-wrap">
                    {JSON.stringify(item.seed_provenance, null, 2)}
                  </pre>
                </details>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  )
}
function Metric({
  title,
  value,
  description,
}: {
  title: string
  value: number | string | null
  description: string
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-2xl font-semibold">{value ?? "Unknown"}</p>
        <p className="text-sm text-muted-foreground">{description}</p>
      </CardContent>
    </Card>
  )
}
function CollectionControls({
  item,
  unavailable,
}: {
  item: CurrentCollection
  unavailable: boolean
}) {
  const mutation = useChangeCollection(item.id)
  const [priority, setPriority] = useState<string | null>(null)
  const [cancel, setCancel] = useState(false)
  const disabled = unavailable || mutation.isPending
  return (
    <Card>
      <CardHeader>
        <CardTitle>Execution controls</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="text-sm text-muted-foreground">
          Pausing stops new work for this execution. Shared acquisitions can
          continue for other executions.
        </p>
        <div className="flex flex-wrap gap-2">
          <Button
            disabled={disabled}
            onClick={() =>
              mutation.mutate({
                action: item.status === "paused" ? "resume" : "pause",
              })
            }
          >
            {item.status === "paused" ? "Resume execution" : "Pause execution"}
          </Button>
          <Button
            variant="outline"
            disabled={disabled}
            onClick={() => setCancel(true)}
          >
            Cancel execution
          </Button>
        </div>
        {cancel && (
          <div
            className="flex flex-col gap-2"
            role="group"
            aria-label="Confirm cancellation"
          >
            <p>
              Cancel this execution and detach its outstanding interest? Already
              acquired evidence is retained.
            </p>
            <div className="flex gap-2">
              <Button
                variant="destructive"
                disabled={disabled}
                onClick={() =>
                  mutation.mutate(
                    { action: "cancel" },
                    { onSuccess: () => setCancel(false) }
                  )
                }
              >
                Confirm cancellation
              </Button>
              <Button
                variant="outline"
                disabled={disabled}
                onClick={() => setCancel(false)}
              >
                Keep execution
              </Button>
            </div>
          </div>
        )}
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault()
            const raw = priority ?? String(item.priority)
            const value = Number(raw)
            if (
              !raw.trim() ||
              !Number.isInteger(value) ||
              value < -10 ||
              value > 10
            ) {
              toast.error(
                extractApiError(
                  new Error("Priority must be an integer from -10 to 10.")
                )
              )
              return
            }
            mutation.mutate(
              { priority: value },
              { onSuccess: () => setPriority(null) }
            )
          }}
        >
          <div className="flex flex-col gap-1">
            <label htmlFor="collection-priority">
              Priority (current: {item.priority})
            </label>
            <Input
              id="collection-priority"
              type="number"
              min={-10}
              max={10}
              step={1}
              value={priority ?? item.priority}
              onChange={(event) => setPriority(event.target.value)}
              disabled={disabled}
            />
          </div>
          <Button
            variant="outline"
            type="submit"
            disabled={disabled || priority === null}
          >
            Set priority
          </Button>
        </form>
        <p className="text-sm text-muted-foreground">
          Higher priority receives preferential service; it does not guarantee a
          start time.
        </p>
      </CardContent>
    </Card>
  )
}
