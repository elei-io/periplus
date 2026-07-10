import {
  CheckCircle2Icon,
  ExternalLinkIcon,
  FileArchiveIcon,
  FilterIcon,
  RefreshCwIcon,
  RouteIcon,
  XCircleIcon,
} from "lucide-react"
import { useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useCrawlMetrics, useCrawls } from "@/hooks/use-history-data"
import { HistoryMetricsBand } from "@/pages/history/history-metrics"
import { HistoryPagination, HISTORY_PAGE_SIZE } from "@/pages/history/history-pagination"
import type { CrawlFilters, CrawlRecord } from "@/types/history"

const defaultFilters: CrawlFilters = {
  urlPattern: "",
  domain: "",
  success: "all",
  statusCode: "",
  warnings: "all",
}

export function CrawlsPage() {
  const [filters, setFilters] = useState<CrawlFilters>(defaultFilters)
  const [offset, setOffset] = useState(0)
  const crawlsQuery = useCrawls(filters, {
    limit: HISTORY_PAGE_SIZE,
    offset,
  })
  const metricsQuery = useCrawlMetrics(filters)
  const crawls = crawlsQuery.data?.items ?? []
  const total = crawlsQuery.data?.total ?? 0

  const patchFilters = (patch: Partial<CrawlFilters>) => {
    setOffset(0)
    setFilters((current) => ({ ...current, ...patch }))
  }

  return (
    <div className="flex min-h-0 w-full flex-col gap-4">
      <section className="flex flex-col gap-3 border-b pb-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2">
            <RouteIcon className="size-4 text-muted-foreground" />
            <h1 className="truncate text-lg font-medium">Crawls</h1>
            <Badge variant="outline">{total}</Badge>
          </div>
          <Button
            variant="outline"
            size="sm"
            disabled={crawlsQuery.isFetching}
            onClick={() => void crawlsQuery.refetch()}
          >
            <RefreshCwIcon />
            Refresh
          </Button>
        </div>
        <div className="grid gap-2 xl:grid-cols-[minmax(18rem,1fr)_13rem_10rem_8rem_10rem]">
          <div className="relative">
            <FilterIcon className="pointer-events-none absolute top-1/2 left-3 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="pl-9"
              value={filters.urlPattern}
              placeholder="*example.com/item/*"
              onChange={(event) => patchFilters({ urlPattern: event.target.value })}
            />
          </div>
          <Input
            value={filters.domain}
            placeholder="example.com"
            onChange={(event) => patchFilters({ domain: event.target.value })}
          />
          <FilterSelect
            value={filters.success}
            options={[
              { value: "all", label: "All results" },
              { value: "succeeded", label: "Succeeded" },
              { value: "failed", label: "Failed" },
            ]}
            onChange={(success) => patchFilters({ success: success as CrawlFilters["success"] })}
            aria-label="Success state"
          />
          <Input
            inputMode="numeric"
            value={filters.statusCode}
            placeholder="200"
            onChange={(event) => patchFilters({ statusCode: event.target.value })}
          />
          <FilterSelect
            value={filters.warnings}
            options={[
              { value: "all", label: "All transport" },
              { value: "clean", label: "Clean" },
              { value: "warning", label: "Transport warnings" },
            ]}
            onChange={(warnings) => patchFilters({ warnings: warnings as CrawlFilters["warnings"] })}
            aria-label="Warning state"
          />
        </div>
      </section>

      <HistoryMetricsBand metrics={metricsQuery.data} isLoading={metricsQuery.isLoading} />

      <Table containerClassName="min-h-0 flex-1 rounded-md border bg-card/80">
        <TableHeader>
          <TableRow>
            <TableHead>URL</TableHead>
            <TableHead>Started</TableHead>
            <TableHead>Duration</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Transport Warnings</TableHead>
            <TableHead>Artifacts</TableHead>
            <TableHead>Input hash</TableHead>
            <TableHead className="w-20" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {crawls.map((crawl) => (
            <CrawlRow key={crawl.id} crawl={crawl} />
          ))}
          {!crawlsQuery.isLoading && crawls.length === 0 ? (
            <TableRow>
              <TableCell colSpan={8} className="h-24 text-center text-muted-foreground">
                No crawls match.
              </TableCell>
            </TableRow>
          ) : null}
        </TableBody>
      </Table>

      <HistoryPagination
        total={total}
        limit={crawlsQuery.data?.limit ?? HISTORY_PAGE_SIZE}
        offset={crawlsQuery.data?.offset ?? offset}
        isFetching={crawlsQuery.isFetching}
        onOffsetChange={setOffset}
      />
    </div>
  )
}

function CrawlRow({ crawl }: { crawl: CrawlRecord }) {
  const artifactsHref = `/history/artifacts?url=${encodeURIComponent(crawl.normalized_url)}`
  const detailHref = `/history/crawls/${crawl.id}`

  return (
    <TableRow>
      <TableCell className="max-w-[34rem]">
        <a
          href={detailHref}
          className="block min-w-0 rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
        >
          <span className="block truncate font-medium text-link underline-offset-4 hover:underline">
            {crawl.normalized_url}
          </span>
          <span className="block truncate text-muted-foreground">{crawl.path_name}</span>
        </a>
      </TableCell>
      <TableCell>{formatDate(crawl.started_at)}</TableCell>
      <TableCell>{formatDuration(crawl.duration_ms)}</TableCell>
      <TableCell>
        <Badge variant={crawl.success ? "secondary" : "destructive"}>
          {crawl.success ? <CheckCircle2Icon /> : <XCircleIcon />}
          {crawl.status_code ?? "none"}
        </Badge>
      </TableCell>
      <TableCell>
        <Badge variant={crawl.warning_count > 0 ? "destructive" : "outline"}>
          {crawl.warning_count}
        </Badge>
      </TableCell>
      <TableCell>{crawl.artifact_count}</TableCell>
      <TableCell>
        <span className="font-mono text-[0.7rem] text-muted-foreground">
          {crawl.input_hash.slice(0, 12)}
        </span>
      </TableCell>
      <TableCell>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon-sm"
            nativeButton={false}
            render={<a href={artifactsHref} />}
          >
            <FileArchiveIcon />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            nativeButton={false}
            render={<a href={crawl.normalized_url} target="_blank" rel="noreferrer" />}
          >
            <ExternalLinkIcon />
          </Button>
        </div>
      </TableCell>
    </TableRow>
  )
}

function FilterSelect({
  value,
  options,
  onChange,
  "aria-label": ariaLabel,
}: {
  value: string
  options: Array<{ value: string; label: string }>
  onChange: (value: string) => void
  "aria-label": string
}) {
  const selectedLabel =
    options.find((option) => option.value === value)?.label ?? value

  return (
    <Select
      value={value}
      onValueChange={(nextValue) => {
        if (nextValue !== null) {
          onChange(nextValue)
        }
      }}
    >
      <SelectTrigger aria-label={ariaLabel} className="w-full">
        <span>{selectedLabel}</span>
      </SelectTrigger>
      <SelectContent>
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
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
