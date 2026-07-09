import {
  DatabaseIcon,
  ExternalLinkIcon,
  FileArchiveIcon,
  FilterIcon,
  RefreshCwIcon,
  ShieldOffIcon,
} from "lucide-react"
import { useMemo, useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useInvalidateArtifacts, useUrlMetrics, useUrls } from "@/hooks/use-history-data"
import { HistoryMetricsBand } from "@/pages/history/history-metrics"
import { HistoryPagination, HISTORY_PAGE_SIZE } from "@/pages/history/history-pagination"
import type { UrlFilters, UrlRecord } from "@/types/history"

const defaultFilters: UrlFilters = {
  urlPattern: "",
  domain: "",
}

export function UrlsPage() {
  const [filters, setFilters] = useState<UrlFilters>(defaultFilters)
  const [offset, setOffset] = useState(0)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [confirmOpen, setConfirmOpen] = useState(false)
  const urlsQuery = useUrls(filters, {
    limit: HISTORY_PAGE_SIZE,
    offset,
  })
  const metricsQuery = useUrlMetrics(filters)
  const invalidateMutation = useInvalidateArtifacts()
  const urls = urlsQuery.data?.items ?? []
  const total = urlsQuery.data?.total ?? 0
  const selectedUrls = useMemo(() => {
    return urls.filter((url) => selectedIds.has(url.id))
  }, [selectedIds, urls])
  const allVisibleSelected = urls.length > 0 && urls.every((url) => selectedIds.has(url.id))
  const selectedActiveArtifacts = selectedUrls.reduce(
    (sum, url) => sum + url.active_artifact_count,
    0
  )
  const selectedCacheEligible = selectedUrls.reduce(
    (sum, url) => sum + url.cache_eligible_count,
    0
  )

  const patchFilters = (patch: Partial<UrlFilters>) => {
    setOffset(0)
    setSelectedIds(new Set())
    setFilters((current) => ({ ...current, ...patch }))
  }

  const toggleSelected = (id: string) => {
    setSelectedIds((current) => {
      const next = new Set(current)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }

  const toggleVisible = () => {
    setSelectedIds((current) => {
      if (allVisibleSelected) {
        const next = new Set(current)
        for (const url of urls) {
          next.delete(url.id)
        }
        return next
      }
      return new Set([...current, ...urls.map((url) => url.id)])
    })
  }

  const invalidateSelected = () => {
    invalidateMutation.mutate(
      {
        url_ids: selectedUrls.map((url) => url.id),
        kind: "html",
        reason: "manual-url-selection",
      },
      {
        onSuccess: () => {
          setSelectedIds(new Set())
          setConfirmOpen(false)
        },
      }
    )
  }

  return (
    <div className="flex min-h-0 w-full flex-col gap-4">
      <section className="flex flex-col gap-3 border-b pb-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2">
            <DatabaseIcon className="size-4 text-muted-foreground" />
            <h1 className="truncate text-lg font-medium">URL Cache</h1>
            <Badge variant="outline">{total}</Badge>
            {selectedIds.size > 0 ? (
              <Badge variant="secondary">{selectedIds.size} selected</Badge>
            ) : null}
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={selectedUrls.length === 0 || invalidateMutation.isPending}
              onClick={() => setConfirmOpen(true)}
            >
              <ShieldOffIcon />
              Invalidate
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={urlsQuery.isFetching}
              onClick={() => void urlsQuery.refetch()}
            >
              <RefreshCwIcon />
              Refresh
            </Button>
          </div>
        </div>
        <div className="grid gap-2 lg:grid-cols-[minmax(18rem,1fr)_14rem]">
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
        </div>
      </section>

      <HistoryMetricsBand metrics={metricsQuery.data} isLoading={metricsQuery.isLoading} />

      <Table containerClassName="min-h-0 flex-1 rounded-md border bg-card/80">
        <TableHeader>
          <TableRow>
            <TableHead className="w-10">
              <input
                type="checkbox"
                aria-label="Select visible URLs"
                checked={allVisibleSelected}
                onChange={toggleVisible}
              />
            </TableHead>
            <TableHead>URL</TableHead>
            <TableHead>Domain</TableHead>
            <TableHead>Cache</TableHead>
            <TableHead>Crawls</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Warnings</TableHead>
            <TableHead>Latest</TableHead>
            <TableHead className="w-20" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {urls.map((url) => (
            <UrlRow
              key={url.id}
              url={url}
              selected={selectedIds.has(url.id)}
              onSelectedChange={() => toggleSelected(url.id)}
            />
          ))}
          {!urlsQuery.isLoading && urls.length === 0 ? (
            <TableRow>
              <TableCell colSpan={9} className="h-24 text-center text-muted-foreground">
                No URLs match.
              </TableCell>
            </TableRow>
          ) : null}
        </TableBody>
      </Table>

      <HistoryPagination
        total={total}
        limit={urlsQuery.data?.limit ?? HISTORY_PAGE_SIZE}
        offset={urlsQuery.data?.offset ?? offset}
        isFetching={urlsQuery.isFetching}
        onOffsetChange={setOffset}
      />

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Invalidate URL cache</DialogTitle>
            <DialogDescription>
              Active HTML artifacts for the selected URLs will stop being used for cache hits.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-2 rounded-md border bg-card/80 p-3 text-xs">
            <SummaryRow label="Selected URLs" value={selectedUrls.length} />
            <SummaryRow label="Active artifacts" value={selectedActiveArtifacts} />
            <SummaryRow label="Cache eligible" value={selectedCacheEligible} />
          </div>
          <DialogFooter showCloseButton>
            <Button
              variant="destructive"
              disabled={selectedUrls.length === 0 || invalidateMutation.isPending}
              onClick={invalidateSelected}
            >
              <ShieldOffIcon />
              Invalidate
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function UrlRow({
  url,
  selected,
  onSelectedChange,
}: {
  url: UrlRecord
  selected: boolean
  onSelectedChange: () => void
}) {
  const artifactsHref = `/history/artifacts?url=${encodeURIComponent(url.normalized_url)}`
  const detailHref = `/history/urls/${url.id}`

  return (
    <TableRow>
      <TableCell>
        <input
          type="checkbox"
          aria-label={`Select ${url.normalized_url}`}
          checked={selected}
          onChange={onSelectedChange}
        />
      </TableCell>
      <TableCell className="max-w-[36rem]">
        <a
          href={detailHref}
          className="block min-w-0 rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
        >
          <span className="block truncate font-medium text-link underline-offset-4 hover:underline">
            {url.normalized_url}
          </span>
          <span className="block truncate text-muted-foreground">{url.path}</span>
        </a>
      </TableCell>
      <TableCell>{url.domain}</TableCell>
      <TableCell>
        <div className="flex flex-wrap items-center gap-1">
          <Badge variant={url.cache_eligible_count > 0 ? "secondary" : "outline"}>
            {url.cache_eligible_count} eligible
          </Badge>
          <Badge variant="outline">{url.active_artifact_count} active</Badge>
          {url.invalidated_artifact_count > 0 ? (
            <Badge variant="destructive">{url.invalidated_artifact_count} invalid</Badge>
          ) : null}
        </div>
      </TableCell>
      <TableCell>{url.crawl_count}</TableCell>
      <TableCell>
        <Badge variant={statusVariant(url.latest_status_code)}>
          {url.latest_status_code ?? "none"}
        </Badge>
      </TableCell>
      <TableCell>
        <div className="flex flex-wrap items-center gap-1">
          <Badge variant={url.warning_count > 0 ? "destructive" : "outline"}>
            {url.warning_count}
          </Badge>
          {url.artifact_warning_count > 0 ? (
            <Badge variant="outline">{url.artifact_warning_count} artifact</Badge>
          ) : null}
          {url.crawl_warning_count > 0 ? (
            <Badge variant="outline">{url.crawl_warning_count} crawl</Badge>
          ) : null}
        </div>
      </TableCell>
      <TableCell>
        <div className="grid gap-1 text-xs">
          <span>{formatDate(url.latest_artifact_at)}</span>
          <span className="text-muted-foreground">{formatDate(url.latest_crawl_at)}</span>
        </div>
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
            render={<a href={url.normalized_url} target="_blank" rel="noreferrer" />}
          >
            <ExternalLinkIcon />
          </Button>
        </div>
      </TableCell>
    </TableRow>
  )
}

function SummaryRow({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium">{value}</span>
    </div>
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

function formatDate(value: string | null) {
  if (!value) {
    return ""
  }

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value))
}
