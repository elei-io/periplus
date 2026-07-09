import {
  ArrowLeftIcon,
  CheckCircle2Icon,
  ExternalLinkIcon,
  FileArchiveIcon,
  RouteIcon,
  ShieldAlertIcon,
  XCircleIcon,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useCrawl } from "@/hooks/use-history-data"
import type { ArtifactRecord, CrawlDetailRecord } from "@/types/history"

export function CrawlDetailPage({ crawlId }: { crawlId: string }) {
  const crawlQuery = useCrawl(crawlId)
  const crawl = crawlQuery.data

  if (crawlQuery.isLoading) {
    return <CenteredText>Loading crawl...</CenteredText>
  }

  if (!crawl) {
    return (
      <div className="flex min-h-0 w-full flex-col gap-3">
        <BackButton />
        <div className="rounded-md border bg-card/80 p-4 text-sm text-muted-foreground">
          Crawl not found.
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-0 w-full flex-col gap-4 overflow-y-auto">
      <section className="flex flex-wrap items-center justify-between gap-3 border-b pb-4">
        <div className="flex min-w-0 items-center gap-2">
          <RouteIcon className="size-4 text-muted-foreground" />
          <h1 className="truncate text-lg font-medium">Crawl</h1>
          <Badge variant={crawl.success ? "secondary" : "destructive"}>
            {crawl.success ? <CheckCircle2Icon /> : <XCircleIcon />}
            {crawl.status_code ?? "No status"}
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          <BackButton />
          <Button
            variant="outline"
            size="sm"
            nativeButton={false}
            render={<a href={crawl.normalized_url} target="_blank" rel="noreferrer" />}
          >
            <ExternalLinkIcon />
            Open URL
          </Button>
        </div>
      </section>

      <section className="grid gap-3 lg:grid-cols-2">
        <DetailGroup
          title="Visit"
          items={[
            ["Crawl ID", crawl.id],
            ["URL ID", crawl.url_id],
            ["Task Run ID", crawl.task_run_id],
            ["Started", formatDate(crawl.started_at)],
            ["Finished", formatDate(crawl.finished_at)],
            ["Duration", formatDuration(crawl.duration_ms)],
            ["Retry Count", String(crawl.retry_count)],
            ["Input Hash", crawl.input_hash],
          ]}
        />
        <DetailGroup
          title="URL"
          items={[
            ["URL", crawl.url],
            ["Normalized", crawl.normalized_url],
            ["Domain", crawl.domain],
            ["Path", crawl.path_name],
            ["Artifacts", String(crawl.artifact_count)],
            ["Warnings", String(crawl.warning_count)],
            ["Error", crawl.error_message],
          ]}
        />
      </section>

      <WarningDetails crawl={crawl} />

      <section className="rounded-md border bg-card/80 p-3">
        <div className="mb-2 flex items-center gap-2">
          <FileArchiveIcon className="size-4 text-muted-foreground" />
          <h2 className="text-sm font-medium">Artifacts</h2>
          <Badge variant="outline">{crawl.artifacts.length}</Badge>
        </div>
        <ArtifactsTable artifacts={crawl.artifacts} />
      </section>

      <section className="grid gap-3 xl:grid-cols-2">
        <JsonPanel title="Inputs" value={crawl.inputs_json} />
        <JsonPanel title="Redirects" value={crawl.redirects_json} />
        <JsonPanel title="Errors" value={crawl.errors_json} />
        <JsonPanel title="Meta" value={crawl.meta} />
      </section>
    </div>
  )
}

function BackButton() {
  return (
    <Button
      variant="outline"
      size="sm"
      nativeButton={false}
      render={<a href="/history/crawls" />}
    >
      <ArrowLeftIcon />
      Crawls
    </Button>
  )
}

function WarningDetails({ crawl }: { crawl: CrawlDetailRecord }) {
  const warnings = parseWarnings(crawl.warnings_json.warnings)

  return (
    <section className="rounded-md border bg-card/80 p-3">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <ShieldAlertIcon className="size-4 text-muted-foreground" />
          <h2 className="text-sm font-medium">Warnings</h2>
        </div>
        <Badge variant={crawl.warning_count > 0 ? "destructive" : "outline"}>
          {crawl.warning_count} total
        </Badge>
      </div>
      {warnings.length > 0 ? (
        <div className="grid gap-3">
          {warnings.map((warning) => (
            <article key={warning.code} className="grid gap-3 rounded-md border bg-muted/30 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">{warning.name}</div>
                  <div className="truncate font-mono text-xs text-muted-foreground">{warning.code}</div>
                </div>
                <Badge variant="outline">{warning.signals.length} signals</Badge>
              </div>
              {warning.signals.length > 0 ? (
                <dl className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                  {warning.signals.map((signal) => (
                    <div key={signal.name} className="rounded-md bg-background/70 px-2 py-1.5">
                      <dt className="truncate text-xs text-muted-foreground">{signal.name}</dt>
                      <dd className="font-mono text-sm">{String(signal.value)}</dd>
                    </div>
                  ))}
                </dl>
              ) : null}
              {warning.description ? (
                <p className="text-xs leading-5 text-muted-foreground">{warning.description}</p>
              ) : null}
            </article>
          ))}
        </div>
      ) : (
        <div className="text-xs text-muted-foreground">No warning details.</div>
      )}
    </section>
  )
}

function ArtifactsTable({ artifacts }: { artifacts: ArtifactRecord[] }) {
  if (artifacts.length === 0) {
    return <div className="text-xs text-muted-foreground">No artifacts.</div>
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Kind</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Warnings</TableHead>
          <TableHead>Size</TableHead>
          <TableHead>Created</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {artifacts.map((artifact) => (
          <TableRow key={artifact.id}>
            <TableCell>
              <a className="text-link hover:underline" href={`/history/artifacts/${artifact.id}`}>
                {artifact.kind}
              </a>
            </TableCell>
            <TableCell>{artifact.invalidated_at ? "Invalidated" : "Active"}</TableCell>
            <TableCell>{artifact.warning_count}</TableCell>
            <TableCell>{formatBytes(artifact.size_bytes)}</TableCell>
            <TableCell>{formatDate(artifact.created_at)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function DetailGroup({
  title,
  items,
}: {
  title: string
  items: Array<[string, string | null]>
}) {
  return (
    <section className="rounded-md border bg-card/80 p-3">
      <h2 className="mb-2 text-sm font-medium">{title}</h2>
      <dl className="grid gap-2 text-xs">
        {items.map(([label, value]) => (
          <div key={label} className="grid gap-1 md:grid-cols-[9rem_minmax(0,1fr)]">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="min-w-0 break-words font-mono">{value || "-"}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}

function JsonPanel({ title, value }: { title: string; value: unknown }) {
  return (
    <section className="rounded-md border bg-card/80 p-3">
      <h2 className="mb-2 text-sm font-medium">{title}</h2>
      <pre className="max-h-[24rem] overflow-auto rounded-md bg-muted/60 p-2 text-xs">
        {JSON.stringify(value ?? {}, null, 2)}
      </pre>
    </section>
  )
}

type CrawlWarning = {
  code: string
  name: string
  description: string | null
  signals: Array<{ name: string; value: unknown }>
}

function parseWarnings(value: unknown): CrawlWarning[] {
  if (!Array.isArray(value)) {
    return []
  }

  return value
    .filter((warning): warning is Record<string, unknown> => {
      return warning !== null && typeof warning === "object"
    })
    .map((warning, index) => {
      const code = typeof warning.code === "string" ? warning.code : `warning-${index + 1}`
      const name = typeof warning.name === "string" ? warning.name : code
      const description =
        typeof warning.description === "string" ? warning.description : null
      const signals = Array.isArray(warning.signals)
        ? warning.signals
            .filter((signal): signal is Record<string, unknown> => {
              return signal !== null && typeof signal === "object"
            })
            .map((signal, signalIndex) => ({
              name:
                typeof signal.name === "string"
                  ? signal.name
                  : `signal-${signalIndex + 1}`,
              value: signal.value ?? 0,
            }))
        : []

      return { code, name, description, signals }
    })
}

function CenteredText({ children }: { children: string }) {
  return (
    <div className="flex min-h-0 w-full items-center justify-center text-sm text-muted-foreground">
      {children}
    </div>
  )
}

function formatBytes(value: number) {
  if (value < 1024) {
    return `${value} B`
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`
  }
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

function formatDuration(value: number | null) {
  if (value === null) {
    return ""
  }
  if (value < 1000) {
    return `${value} ms`
  }
  return `${(value / 1000).toFixed(2)}s`
}

function formatDate(value: string | null) {
  if (!value) {
    return ""
  }

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value))
}
