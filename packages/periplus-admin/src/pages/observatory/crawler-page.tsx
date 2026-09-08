import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useCrawlerLive } from "@/hooks/use-crawler-live"
import { extractApiError } from "@/lib/api"
import { CrawlerControls } from "@/pages/frontier/controls-page"

const count = (value: number | null | undefined) =>
  value == null ? "—" : value.toLocaleString()
const at = (value: string | null) =>
  value ? new Date(value).toLocaleString() : "—"
const elapsed = (start: string | null, now: string) => {
  if (!start) return "Not started"
  const seconds = Math.max(
    0,
    Math.floor((Date.parse(now) - Date.parse(start)) / 1000)
  )
  return seconds < 60
    ? `${seconds}s`
    : seconds < 3600
      ? `${Math.floor(seconds / 60)}m ${seconds % 60}s`
      : `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
}
const dependency: Record<string, string> = {
  ingestion_delivery_unavailable: "Ingestion delivery unavailable",
  storage_unavailable: "Object storage unavailable",
  cdp_unavailable: "Acquisition service (CDP) unavailable",
}

export function CrawlerPage() {
  return (
    <CrawlerControls>
      <CrawlerActivity />
    </CrawlerControls>
  )
}

function CrawlerActivity() {
  const query = useCrawlerLive()
  const data = query.data
  const current = data?.current
  const workers = data?.workers
  return (
    <section
      className="flex min-w-0 flex-col gap-4"
      aria-label="Crawler activity"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Activity and workers</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            Global controls above include all work. Acquisition previews and
            historical counts below cover public work only.
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          disabled={query.isFetching}
          onClick={() => void query.refetch()}
        >
          Refresh activity
        </Button>
      </div>
      {query.error && (
        <p
          role="alert"
          className="rounded-md border border-destructive/40 p-3 text-sm"
        >
          {data ? "Activity may be stale. " : "Activity unavailable. "}
          {extractApiError(query.error)}
        </p>
      )}
      {query.isPending && (
        <p role="status" className="text-sm text-muted-foreground">
          Reading crawler activity…
        </p>
      )}
      {data && current && workers && (
        <>
          <Card>
            <CardHeader>
              <CardTitle>Worker readiness</CardTitle>
              <CardDescription>
                Recent process reports across the crawler fleet. Readiness
                describes dependency checks, not reserved acquisition capacity.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
                {[
                  ["Reporting", workers.reported_workers],
                  ["Ready", workers.ready_workers],
                  ["Blocked", workers.blocked_workers],
                  ["Checking", workers.checking_workers],
                  ["Unknown", workers.unknown_workers],
                ].map(([label, value]) => (
                  <div key={label}>
                    <p className="text-2xl font-semibold tabular-nums">
                      {count(value as number | null)}
                    </p>
                    <p className="text-xs text-muted-foreground">{label}</p>
                  </div>
                ))}
              </div>
              {workers.state !== "observed" && (
                <p className="text-sm text-muted-foreground">
                  {workers.state === "unavailable"
                    ? "Worker presence is unavailable."
                    : "No recent worker reports. Availability is unknown."}
                </p>
              )}
              {workers.waiting_reasons.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {workers.waiting_reasons.map((reason) => (
                    <Badge key={reason} variant="destructive">
                      {dependency[reason] ?? reason}
                    </Badge>
                  ))}
                </div>
              )}
              <p className="border-t pt-3 text-xs text-muted-foreground">
                Reported {at(workers.as_of)}.{" "}
                {workers.more_workers ? "Limited to 128 reports. " : ""}
                {workers.excluded_reports
                  ? `${count(workers.excluded_reports)} stale or invalid reports excluded. `
                  : ""}
                Individual worker identities and work assignments are not
                exposed by this feed.
              </p>
            </CardContent>
          </Card>
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>
                  Active acquisitions <Badge variant="outline">Public</Badge>
                </CardTitle>
                <CardDescription>
                  {count(current.started)} started · {count(current.dispatched)}{" "}
                  dispatched, including started captures.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {current.active.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    No dispatched public acquisitions.
                  </p>
                ) : (
                  current.active.map((item) => (
                    <div
                      key={item.acquisition_id}
                      className="rounded-md border p-3"
                    >
                      <a
                        className="text-sm break-all underline underline-offset-4"
                        href={`/frontier/items/${encodeURIComponent(item.acquisition_id)}`}
                      >
                        {item.requested_url}
                      </a>
                      <div className="mt-2 flex items-center gap-2">
                        <Badge variant="secondary">
                          {item.attempt_started_at
                            ? "Capture started"
                            : "Dispatched"}
                        </Badge>
                        <span className="text-xs text-muted-foreground">
                          {item.attempt_started_at
                            ? `${elapsed(item.attempt_started_at, current.as_of)} elapsed`
                            : "Physical attempt has not started"}
                        </span>
                      </div>
                    </div>
                  ))
                )}
                {current.more_active && (
                  <p className="text-xs text-muted-foreground">
                    Showing 12 dispatched acquisitions. More work is active.
                  </p>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>
                  Waiting acquisitions <Badge variant="outline">Public</Badge>
                </CardTitle>
                <CardDescription>
                  {count(current.queued)} queued or retrying. Oldest admitted:{" "}
                  {at(current.oldest_wait_at)}.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {current.upcoming.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    No pending public acquisitions.
                  </p>
                ) : (
                  current.upcoming.map((item) => (
                    <div
                      key={item.acquisition_id}
                      className="rounded-md border p-3"
                    >
                      <a
                        className="text-sm break-all underline underline-offset-4"
                        href={`/frontier/items/${encodeURIComponent(item.acquisition_id)}`}
                      >
                        {item.requested_url}
                      </a>
                      <p className="mt-2 text-xs text-muted-foreground">
                        Waiting {elapsed(item.admitted_at, current.as_of)} ·{" "}
                        {Date.parse(item.retry_not_before) >
                        Date.parse(current.as_of)
                          ? `Not before ${at(item.retry_not_before)}`
                          : "Open details for current constraints"}
                      </p>
                    </div>
                  ))
                )}
                <p className="text-xs text-muted-foreground">
                  Oldest five pending items, not dispatch order. Open an
                  acquisition for domain pacing, policy and budget constraints;
                  the feed does not partition runnable and deferred work.
                </p>
              </CardContent>
            </Card>
          </div>
          <Card>
            <CardHeader>
              <CardTitle>Domains with current work</CardTitle>
              <CardDescription>
                Public acquisitions, up to 10 domains. Started captures are
                included in dispatched counts.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Domain</TableHead>
                    <TableHead>Waiting</TableHead>
                    <TableHead>Dispatched</TableHead>
                    <TableHead>Started</TableHead>
                    <TableHead>Oldest waiting</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {current.domains.map((domain) => (
                    <TableRow key={domain.domain}>
                      <TableCell className="font-medium">
                        {domain.domain}
                      </TableCell>
                      <TableCell>{count(domain.queued)}</TableCell>
                      <TableCell>{count(domain.dispatched)}</TableCell>
                      <TableCell>{count(domain.started)}</TableCell>
                      <TableCell>{at(domain.oldest_wait_at)}</TableCell>
                    </TableRow>
                  ))}
                  {!current.domains.length && (
                    <TableRow>
                      <TableCell
                        colSpan={5}
                        className="py-6 text-center text-muted-foreground"
                      >
                        No public domains currently have queued or dispatched
                        work.
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
              <div className="mt-3 flex flex-wrap justify-between gap-2 text-xs text-muted-foreground">
                <span>
                  {current.more_domains
                    ? "Additional domains are not shown. "
                    : ""}
                  Observed {at(current.as_of)}
                </span>
                <a
                  className="underline underline-offset-4"
                  href="/observatory/domain-policies"
                >
                  Manage domain pacing and concurrency
                </a>
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Recent outcomes</CardTitle>
              <CardDescription>
                Existing committed public crawl evidence. Ingestion delays can
                understate recent activity.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {data.history ? (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Window</TableHead>
                      <TableHead>Attempt starts</TableHead>
                      <TableHead>Successful captures</TableHead>
                      <TableHead>Failed captures</TableHead>
                      <TableHead>Starts / minute</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.history.velocities
                      .filter((window) => window.domain === null)
                      .map((window) => (
                        <TableRow key={window.seconds}>
                          <TableCell>
                            {window.seconds / 60} minute
                            {window.seconds === 60 ? "" : "s"}
                          </TableCell>
                          <TableCell>{count(window.attempt_starts)}</TableCell>
                          <TableCell>
                            {count(window.successful_captures)}
                          </TableCell>
                          <TableCell>{count(window.failed_captures)}</TableCell>
                          <TableCell>
                            {window.attempt_starts_per_minute.toLocaleString(
                              undefined,
                              { maximumFractionDigits: 1 }
                            )}
                          </TableCell>
                        </TableRow>
                      ))}
                  </TableBody>
                </Table>
              ) : (
                <p className="text-sm text-muted-foreground">
                  Committed history is unavailable. Current frontier state
                  remains separate.
                </p>
              )}
              <div className="divide-y">
                {data.recent.map((item) => (
                  <div key={item.observation_id} className="py-3">
                    <p className="text-sm break-all">{item.requested_url}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Successful capture · {at(item.completed_at)} ·{" "}
                      {item.evidence_committed
                        ? "Evidence committed"
                        : "Evidence commit not verified"}
                    </p>
                  </div>
                ))}
              </div>
              {!data.recent.length && (
                <p className="text-sm text-muted-foreground">
                  No recent successful captures in the available records.
                </p>
              )}
              <p className="text-xs text-muted-foreground">
                The capture preview contains successes only. Individual failures
                and uncertain attempts are available through collection and
                acquisition details, not this feed.{" "}
                <a
                  href="/observatory/executions"
                  className="underline underline-offset-4"
                >
                  Inspect requests
                </a>
                .
              </p>
            </CardContent>
          </Card>
          <p className="text-xs text-muted-foreground">
            Activity refreshes every 5 seconds. No telemetry is stored by this
            page. Settings edits update the existing crawler policy; they do not
            stop worker processes or interrupt started captures.
          </p>
        </>
      )}
    </section>
  )
}
