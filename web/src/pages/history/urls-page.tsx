import { DatabaseIcon, ExternalLinkIcon, FileArchiveIcon, FilterIcon, RefreshCwIcon } from "lucide-react"
import { useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useUrls } from "@/hooks/use-history-data"
import { HistoryPagination, HISTORY_PAGE_SIZE } from "@/pages/history/history-pagination"
import type { UrlFilters, UrlRecord } from "@/types/history"

const defaultFilters: UrlFilters = {
  urlPattern: "",
  domain: "",
}

export function UrlsPage() {
  const [filters, setFilters] = useState<UrlFilters>(defaultFilters)
  const [offset, setOffset] = useState(0)
  const urlsQuery = useUrls(filters, {
    limit: HISTORY_PAGE_SIZE,
    offset,
  })
  const urls = urlsQuery.data?.items ?? []
  const total = urlsQuery.data?.total ?? 0

  const patchFilters = (patch: Partial<UrlFilters>) => {
    setOffset(0)
    setFilters((current) => ({ ...current, ...patch }))
  }

  return (
    <div className="flex min-h-0 w-full flex-col gap-4">
      <section className="flex flex-col gap-3 border-b pb-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2">
            <DatabaseIcon className="size-4 text-muted-foreground" />
            <h1 className="truncate text-lg font-medium">URLs</h1>
            <Badge variant="outline">{total}</Badge>
          </div>
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

      <Table containerClassName="min-h-0 flex-1 rounded-md border bg-card/80">
        <TableHeader>
          <TableRow>
            <TableHead>URL</TableHead>
            <TableHead>Domain</TableHead>
            <TableHead>Crawls</TableHead>
            <TableHead>Artifacts</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Warnings</TableHead>
            <TableHead>Latest crawl</TableHead>
            <TableHead className="w-20" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {urls.map((url) => (
            <UrlRow key={url.id} url={url} />
          ))}
          {!urlsQuery.isLoading && urls.length === 0 ? (
            <TableRow>
              <TableCell colSpan={8} className="h-24 text-center text-muted-foreground">
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
    </div>
  )
}

function UrlRow({ url }: { url: UrlRecord }) {
  const artifactsHref = `/history/artifacts?url=${encodeURIComponent(url.normalized_url)}`
  const detailHref = `/history/urls/${url.id}`

  return (
    <TableRow>
      <TableCell className="max-w-[36rem]">
        <a
          href={detailHref}
          className="block min-w-0 rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
        >
          <span className="block truncate font-medium text-primary underline-offset-4 hover:underline">
            {url.normalized_url}
          </span>
          <span className="block truncate text-muted-foreground">{url.path}</span>
        </a>
      </TableCell>
      <TableCell>{url.domain}</TableCell>
      <TableCell>{url.crawl_count}</TableCell>
      <TableCell>{url.artifact_count}</TableCell>
      <TableCell>
        <Badge variant={statusVariant(url.latest_status_code)}>
          {url.latest_status_code ?? "none"}
        </Badge>
      </TableCell>
      <TableCell>
        <Badge variant={url.warning_count > 0 ? "destructive" : "outline"}>
          {url.warning_count}
        </Badge>
      </TableCell>
      <TableCell>{formatDate(url.latest_crawl_at)}</TableCell>
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
