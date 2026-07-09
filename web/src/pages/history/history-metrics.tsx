import { InfoIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import type { HistoryMetricsResponse, MetricBreakdown, MetricCard } from "@/types/history"

export function HistoryMetricsBand({
  metrics,
  isLoading,
}: {
  metrics: HistoryMetricsResponse | undefined
  isLoading: boolean
}) {
  const cards = metrics?.cards ?? []
  const breakdowns = metrics?.breakdowns ?? []

  return (
    <section className="grid items-start gap-2 xl:grid-cols-[minmax(0,0.95fr)_minmax(26rem,1fr)]">
      <div className="grid auto-rows-min gap-2 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-5">
        {cards.length > 0 ? (
          cards.map((card) => <MetricCardItem key={card.metric} card={card} />)
        ) : (
          <EmptyMetricCards isLoading={isLoading} />
        )}
      </div>
      <div className="grid auto-rows-min gap-2 lg:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
        {breakdowns.length > 0 ? (
          breakdowns.map((breakdown) => (
            <MetricBreakdownItem key={`${breakdown.metric}-${breakdown.label}`} breakdown={breakdown} />
          ))
        ) : (
          <EmptyBreakdowns isLoading={isLoading} />
        )}
      </div>
    </section>
  )
}

function MetricCardItem({ card }: { card: MetricCard }) {
  const help = card.description ?? metricHelp(card.metric, card.label)

  return (
    <div
      className={cn(
        "min-w-0 rounded-md border bg-card/80 px-3 py-2.5",
        card.tone === "warning" && "border-destructive/40 bg-destructive/5"
      )}
    >
      <div className="flex min-w-0 items-center justify-between gap-1.5">
        <span className="truncate text-xs text-muted-foreground">{card.label}</span>
        <MetricTooltip metric={card.metric} help={help} />
      </div>
      <div
        className={cn(
          "mt-1 truncate text-lg font-medium",
          card.tone === "warning" && "text-destructive"
        )}
      >
        {formatMetricValue(card.value, card.unit)}
      </div>
    </div>
  )
}

function MetricBreakdownItem({ breakdown }: { breakdown: MetricBreakdown }) {
  const maxValue = Math.max(...breakdown.items.map((item) => item.value), 0)
  const help = metricHelp(breakdown.metric, breakdown.label)

  return (
    <div className="min-w-0 rounded-md border bg-card/80 px-3 py-2.5">
      <div className="mb-2 flex min-w-0 items-center justify-between gap-2">
        <span className="truncate text-xs font-medium text-muted-foreground">
          {breakdown.label}
        </span>
        <MetricTooltip metric={breakdown.metric} help={help} />
      </div>
      <div className="grid gap-1.5">
        {breakdown.items.length > 0 ? (
          breakdown.items.slice(0, 10).map((item) => {
            const width = maxValue > 0 ? Math.max(4, (item.value / maxValue) * 100) : 0
            return (
              <div key={`${item.metric}-${item.label}`} className="grid gap-1">
                <div className="flex min-w-0 items-center justify-between gap-3 text-xs">
                  <span className="truncate" title={item.label}>
                    {item.label}
                  </span>
                  <span className="shrink-0 font-mono text-muted-foreground">
                    {formatMetricValue(item.value, breakdown.unit)}
                  </span>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full rounded-full bg-primary"
                    style={{ width: `${width}%` }}
                  />
                </div>
              </div>
            )
          })
        ) : (
          <div className="text-xs text-muted-foreground">No samples</div>
        )}
      </div>
    </div>
  )
}

function EmptyMetricCards({ isLoading }: { isLoading: boolean }) {
  return (
    <>
      {[0, 1, 2].map((index) => (
        <div key={index} className="rounded-md border bg-card/80 px-3 py-2.5">
          <div className="h-3 w-20 rounded bg-muted" />
          <div className="mt-2 h-6 w-16 rounded bg-muted" />
          {!isLoading ? <span className="sr-only">No metrics</span> : null}
        </div>
      ))}
    </>
  )
}

function EmptyBreakdowns({ isLoading }: { isLoading: boolean }) {
  return (
    <div className="rounded-md border bg-card/80 px-3 py-2.5">
      <div className="h-3 w-28 rounded bg-muted" />
      <div className="mt-3 grid gap-2">
        {[0, 1, 2].map((index) => (
          <div key={index} className="h-5 rounded bg-muted" />
        ))}
      </div>
      {!isLoading ? <span className="sr-only">No breakdowns</span> : null}
    </div>
  )
}

function MetricTooltip({ metric, help }: { metric: string; help: string }) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            variant="ghost"
            size="icon-sm"
            className="-mr-1 size-6 text-muted-foreground hover:text-foreground"
            aria-label={`About ${metric}`}
          />
        }
      >
        <InfoIcon className="size-3.5" />
      </TooltipTrigger>
      <TooltipContent side="top" align="end" className="max-w-72">
        <div className="grid gap-1">
          <span>{help}</span>
          <span className="font-mono text-[0.68rem] text-muted-foreground">{metric}</span>
        </div>
      </TooltipContent>
    </Tooltip>
  )
}

function metricHelp(metric: string, label: string) {
  const descriptions: Record<string, string> = {
    atlas_crawls_total: "Crawls recorded in the selected filter set and recent metrics window.",
    atlas_crawl_success_ratio: "Share of recent crawls that completed successfully.",
    atlas_crawl_warnings_total: "Non-fatal crawl warnings captured during recent visits.",
    atlas_crawl_duration_seconds_p95: "95th percentile crawl duration for the recent window.",
    atlas_urls_total: "Unique URLs known to Atlas for the current filters.",
    atlas_urls_recently_crawled_total: "Known URLs with at least one crawl in the recent metrics window.",
    atlas_url_visit_rate_per_hour: "Average crawls per hour in the recent metrics window.",
    atlas_artifact_cache_hit_ratio: "Share of cache events that reused an existing artifact instead of producing a fresh one.",
    atlas_url_warning_density_ratio: "Share of filtered URLs with crawl or artifact warnings.",
    atlas_artifact_cache_events_total: "Cache hits versus fresh artifact production in the recent metrics window.",
    atlas_artifacts_total: "Byte artifacts matching the current filters.",
    atlas_artifacts_cache_eligible_total: "Active artifacts currently eligible for cache reuse.",
    atlas_artifacts_warning_total: "Active artifacts with content-quality warnings.",
    atlas_artifacts_invalidated_total: "Artifacts that have been manually or automatically invalidated.",
    atlas_artifact_bytes_total: "Total stored bytes for matching artifacts.",
    atlas_extract_schemas_total: "Extraction schemas matching the current filters.",
    atlas_extract_schemas_enabled_total: "Matching schemas that are enabled for reuse.",
    atlas_extract_schema_uses_total: "Task runs that used matching extraction schemas.",
    atlas_extract_schema_reuse_ratio: "Share of schema uses beyond each schema's first use.",
    atlas_extract_schema_failures_total: "Schemas with failures or warnings that may need attention.",
  }

  return descriptions[metric] ?? label
}

function formatMetricValue(value: number | string, unit: string | null) {
  if (typeof value === "string") {
    return value
  }
  if (unit === "%") {
    return `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })}%`
  }
  if (unit === "s") {
    return `${value.toLocaleString(undefined, { maximumFractionDigits: 3 })}s`
  }
  if (unit === "/h") {
    return `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}/h`
  }
  if (unit === "bytes") {
    return formatBytes(value)
  }
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 })
}

function formatBytes(value: number) {
  if (value < 1024) {
    return `${value} B`
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`
  }
  if (value < 1024 * 1024 * 1024) {
    return `${(value / 1024 / 1024).toFixed(1)} MB`
  }
  return `${(value / 1024 / 1024 / 1024).toFixed(1)} GB`
}
