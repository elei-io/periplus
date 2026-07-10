import {
  ArrowLeftIcon,
  ExternalLinkIcon,
  FileArchiveIcon,
  ShieldAlertIcon,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { useArtifact } from "@/hooks/use-history-data"
import type { ArtifactDetailRecord } from "@/types/history"

export function ArtifactDetailPage({ artifactId }: { artifactId: string }) {
  const artifactQuery = useArtifact(artifactId)
  const artifact = artifactQuery.data

  if (artifactQuery.isLoading) {
    return (
      <div className="flex min-h-0 w-full items-center justify-center text-sm text-muted-foreground">
        Loading artifact...
      </div>
    )
  }

  if (!artifact) {
    return (
      <div className="flex min-h-0 w-full flex-col gap-3">
        <BackButton />
        <div className="rounded-md border bg-card/80 p-4 text-sm text-muted-foreground">
          Artifact not found.
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-0 w-full flex-col gap-4 overflow-y-auto">
      <section className="flex flex-wrap items-center justify-between gap-3 border-b pb-4">
        <div className="flex min-w-0 items-center gap-2">
          <FileArchiveIcon className="size-4 text-muted-foreground" />
          <h1 className="truncate text-lg font-medium">Artifact</h1>
          <Badge variant="outline">{artifact.kind}</Badge>
          <Badge variant={artifact.invalidated_at ? "destructive" : "secondary"}>
            {artifact.invalidated_at ? "Invalidated" : "Active"}
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          <BackButton />
          {artifact.normalized_url ? (
            <Button
              variant="outline"
              size="sm"
              nativeButton={false}
              render={<a href={artifact.normalized_url} target="_blank" rel="noreferrer" />}
            >
              <ExternalLinkIcon />
              Open URL
            </Button>
          ) : null}
        </div>
      </section>

      <section className="grid gap-3 lg:grid-cols-2">
        <DetailGroup
          title="Identity"
          items={[
            ["Artifact ID", artifact.id],
            ["Crawl ID", artifact.crawl_id],
            ["URL ID", artifact.url_id],
            ["Task Run ID", artifact.task_run_id],
            ["Kind", artifact.kind],
            ["Content Type", artifact.content_type],
          ]}
        />
        <DetailGroup
          title="Storage"
          items={[
            ["Path", artifact.path],
            ["Size", formatBytes(artifact.size_bytes)],
            ["SHA-256", artifact.sha256],
            ["Input Hash", artifact.input_hash],
            ["Created", formatDate(artifact.created_at)],
          ]}
        />
        <DetailGroup
          title="URL"
          items={[
            ["URL", artifact.url],
            ["Normalized", artifact.normalized_url],
            ["Domain", artifact.domain],
            ["Path", artifact.path_name],
          ]}
        />
        <DetailGroup
          title="Cache"
          items={[
            ["Quality Warnings", String(artifact.warning_count)],
            ["Invalidated At", formatDate(artifact.invalidated_at)],
            ["Invalidated Reason", artifact.invalidated_reason],
          ]}
        />
      </section>

      <WarningDetails artifact={artifact} />

      <section className="grid gap-3">
        <JsonPanel title="Meta" value={artifact.meta} />
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
      render={<a href="/history/artifacts" />}
    >
      <ArrowLeftIcon />
      Artifacts
    </Button>
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

function WarningDetails({ artifact }: { artifact: ArtifactDetailRecord }) {
  const warnings = parseWarnings(artifact.warnings_json.warnings)

  return (
    <section className="rounded-md border bg-card/80 p-3">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <ShieldAlertIcon className="size-4 text-muted-foreground" />
          <h2 className="text-sm font-medium">Quality Warnings</h2>
        </div>
        <Badge variant={artifact.warning_count > 0 ? "destructive" : "outline"}>
          {artifact.warning_count} total
        </Badge>
      </div>
      {warnings.length > 0 ? (
        <div className="grid gap-3">
          {warnings.map((warning) => (
            <article
              key={warning.code}
              className="grid gap-3 rounded-md border bg-muted/30 p-3"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">{warning.name}</div>
                  <div className="truncate font-mono text-xs text-muted-foreground">
                    {warning.code}
                  </div>
                </div>
                <Badge variant="outline">{warning.signals.length} signals</Badge>
              </div>
              {warning.signals.length > 0 ? (
                <dl className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                  {warning.signals.map((signal) => (
                    <div
                      key={signal.name}
                      className="rounded-md bg-background/70 px-2 py-1.5"
                    >
                      <dt className="truncate text-xs text-muted-foreground">
                        {signal.name}
                      </dt>
                      <dd className="font-mono text-sm">{String(signal.value)}</dd>
                    </div>
                  ))}
                </dl>
              ) : null}
              {warning.description ? (
                <p className="text-xs leading-5 text-muted-foreground">
                  {warning.description}
                </p>
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

type ArtifactWarning = {
  code: string
  name: string
  description: string | null
  signals: Array<{ name: string; value: unknown }>
}

function parseWarnings(value: unknown): ArtifactWarning[] {
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

function JsonPanel({
  title,
  value,
  compact = false,
}: {
  title: string
  value: unknown
  compact?: boolean
}) {
  return (
    <section className={compact ? "mt-3" : "rounded-md border bg-card/80 p-3"}>
      <h2 className="mb-2 text-sm font-medium">{title}</h2>
      <pre className="max-h-[24rem] overflow-auto rounded-md bg-muted/60 p-2 text-xs">
        {JSON.stringify(value ?? {}, null, 2)}
      </pre>
    </section>
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

function formatDate(value: string | null) {
  if (!value) {
    return null
  }

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value))
}
