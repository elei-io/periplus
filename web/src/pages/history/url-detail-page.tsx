import {
  ArrowLeftIcon,
  ExternalLinkIcon,
  FileArchiveIcon,
  LinkIcon,
  RouteIcon,
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
import { useUrl } from "@/hooks/use-history-data"
import type { ArtifactRecord, CrawlRecord } from "@/types/history"

export function UrlDetailPage({ urlId }: { urlId: string }) {
  const urlQuery = useUrl(urlId)
  const url = urlQuery.data

  if (urlQuery.isLoading) {
    return <CenteredText>Loading URL...</CenteredText>
  }

  if (!url) {
    return (
      <div className="flex min-h-0 w-full flex-col gap-3">
        <BackButton />
        <div className="rounded-md border bg-card/80 p-4 text-sm text-muted-foreground">
          URL not found.
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-0 w-full flex-col gap-4 overflow-y-auto">
      <section className="flex flex-wrap items-center justify-between gap-3 border-b pb-4">
        <div className="flex min-w-0 items-center gap-2">
          <LinkIcon className="size-4 text-muted-foreground" />
          <h1 className="truncate text-lg font-medium">URL</h1>
          <Badge variant={statusVariant(url.latest_status_code)}>
            {url.latest_status_code ?? "No crawls"}
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          <BackButton />
          <Button
            variant="outline"
            size="sm"
            nativeButton={false}
            render={<a href={url.normalized_url} target="_blank" rel="noreferrer" />}
          >
            <ExternalLinkIcon />
            Open URL
          </Button>
        </div>
      </section>

      <section className="grid gap-3 lg:grid-cols-2">
        <DetailGroup
          title="Identity"
          items={[
            ["URL ID", url.id],
            ["URL", url.url],
            ["Normalized", url.normalized_url],
            ["Scheme", url.scheme],
            ["Host", url.host],
            ["Domain", url.domain],
            ["Path", url.path],
            ["Query Fingerprint", url.query_fingerprint],
          ]}
        />
        <DetailGroup
          title="Cache Health"
          items={[
            ["Artifacts", String(url.artifact_count)],
            ["Active Artifacts", String(url.active_artifact_count)],
            ["Invalidated Artifacts", String(url.invalidated_artifact_count)],
            ["Cache Eligible", String(url.cache_eligible_count)],
            ["Artifact Warnings", String(url.artifact_warning_count)],
            ["Latest Artifact", formatDate(url.latest_artifact_at)],
          ]}
        />
        <DetailGroup
          title="Crawl Health"
          items={[
            ["Crawls", String(url.crawl_count)],
            ["Crawl Warnings", String(url.crawl_warning_count)],
            ["Total Warnings", String(url.warning_count)],
            ["Latest Status", url.latest_status_code ? String(url.latest_status_code) : null],
            ["Latest Crawl", formatDate(url.latest_crawl_at)],
          ]}
        />
      </section>

      <section className="rounded-md border bg-card/80 p-3">
        <div className="mb-2 flex items-center gap-2">
          <RouteIcon className="size-4 text-muted-foreground" />
          <h2 className="text-sm font-medium">Recent Crawls</h2>
          <Badge variant="outline">{url.recent_crawls.length}</Badge>
        </div>
        <CrawlsTable crawls={url.recent_crawls} />
      </section>

      <section className="rounded-md border bg-card/80 p-3">
        <div className="mb-2 flex items-center gap-2">
          <FileArchiveIcon className="size-4 text-muted-foreground" />
          <h2 className="text-sm font-medium">Artifacts</h2>
          <Badge variant="outline">{url.artifacts.length}</Badge>
        </div>
        <ArtifactsTable artifacts={url.artifacts} />
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
      render={<a href="/history/urls" />}
    >
      <ArrowLeftIcon />
      URLs
    </Button>
  )
}

function CrawlsTable({ crawls }: { crawls: CrawlRecord[] }) {
  if (crawls.length === 0) {
    return <div className="text-xs text-muted-foreground">No crawls.</div>
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Started</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Duration</TableHead>
          <TableHead>Warnings</TableHead>
          <TableHead>Input Hash</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {crawls.map((crawl) => (
          <TableRow key={crawl.id}>
            <TableCell>
              <a className="text-link hover:underline" href={`/history/crawls/${crawl.id}`}>
                {formatDate(crawl.started_at)}
              </a>
            </TableCell>
            <TableCell>
              <Badge variant={crawl.success ? "secondary" : "destructive"}>
                {crawl.status_code ?? "none"}
              </Badge>
            </TableCell>
            <TableCell>{formatDuration(crawl.duration_ms)}</TableCell>
            <TableCell>{crawl.warning_count}</TableCell>
            <TableCell className="font-mono text-[0.7rem] text-muted-foreground">
              {crawl.input_hash.slice(0, 12)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
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

function CenteredText({ children }: { children: string }) {
  return (
    <div className="flex min-h-0 w-full items-center justify-center text-sm text-muted-foreground">
      {children}
    </div>
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

function statusVariant(statusCode: number | null) {
  if (statusCode === null) {
    return "outline"
  }
  if (statusCode >= 200 && statusCode < 400) {
    return "secondary"
  }
  return "destructive"
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
