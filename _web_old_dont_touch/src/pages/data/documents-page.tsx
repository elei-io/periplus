import { format } from "date-fns"
import {
  ArrowDownIcon,
  ArrowUpDownIcon,
  ArrowUpIcon,
  CalendarDaysIcon,
  DatabaseIcon,
  DownloadIcon,
  ExternalLinkIcon,
  FilesIcon,
  FingerprintIcon,
  HardDriveIcon,
  RefreshCwIcon,
  XIcon,
  type LucideIcon,
} from "lucide-react"
import { useEffect, useState } from "react"
import type { DateRange } from "react-day-picker"

import {
  ResourcePagination,
  RESOURCE_PAGE_SIZE,
} from "@/components/resources/resource-pagination"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Calendar } from "@/components/ui/calendar"
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  documentContentUrl,
  useDocumentMediaTypes,
  useDocuments,
} from "@/hooks/use-documents"
import { extractApiError } from "@/lib/api"
import type {
  DocumentRecord,
  DocumentSort,
  SortDirection,
} from "@/types/documents"

export function DocumentsPage() {
  const [urlInput, setUrlInput] = useState("")
  const [urlFilter, setUrlFilter] = useState("")
  const [contentType, setContentType] = useState("all")
  const [dateRange, setDateRange] = useState<DateRange>()
  const [sort, setSort] = useState<DocumentSort>("observed_at")
  const [direction, setDirection] = useState<SortDirection>("desc")
  const [offset, setOffset] = useState(0)

  useEffect(() => {
    const timer = window.setTimeout(() => setUrlFilter(urlInput.trim()), 300)
    return () => window.clearTimeout(timer)
  }, [urlInput])

  const query = useDocuments({
    limit: RESOURCE_PAGE_SIZE,
    offset,
    sort,
    direction,
    contentType: contentType === "all" ? undefined : contentType,
    url: urlFilter || undefined,
    observedFrom: startOfLocalDay(dateRange?.from),
    observedTo: startOfNextLocalDay(dateRange?.to),
  })
  const mediaTypes = useDocumentMediaTypes()
  const documents = query.data?.items ?? []
  const summary = query.data?.summary
  const activeFilterCount =
    Number(Boolean(urlInput)) +
    Number(contentType !== "all") +
    Number(Boolean(dateRange?.from))
  const repeatedDocuments = summary
    ? summary.document_count - summary.unique_content_count
    : 0
  const averageDocumentBytes =
    summary && summary.document_count > 0
      ? summary.logical_bytes / summary.document_count
      : 0

  const updateSort = (next: DocumentSort) => {
    setOffset(0)
    if (sort === next) {
      setDirection(direction === "asc" ? "desc" : "asc")
      return
    }
    setSort(next)
    setDirection(next === "observed_at" ? "desc" : "asc")
  }

  const clearFilters = () => {
    setUrlInput("")
    setUrlFilter("")
    setContentType("all")
    setDateRange(undefined)
    setOffset(0)
  }

  return (
    <div className="flex min-h-0 w-full min-w-0 flex-col gap-4">
      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <SummaryCard
          icon={FilesIcon}
          label="Documents"
          value={summary?.document_count.toLocaleString()}
          description={`${formatBytes(averageDocumentBytes)} average logical size`}
          loading={query.isLoading}
        />
        <SummaryCard
          icon={FingerprintIcon}
          label="Unique content"
          value={summary?.unique_content_count.toLocaleString()}
          description={`${repeatedDocuments.toLocaleString()} repeated observations`}
          loading={query.isLoading}
        />
        <SummaryCard
          icon={DatabaseIcon}
          label="Logical volume"
          value={summary ? formatBytes(summary.logical_bytes) : undefined}
          description="Across matching observations"
          loading={query.isLoading}
        />
        <SummaryCard
          icon={HardDriveIcon}
          label="Owned storage"
          value={summary ? formatBytes(summary.stored_bytes) : undefined}
          description="Deduplicated repository objects"
          loading={query.isLoading}
        />
      </section>

      <Card size="sm" className="overflow-visible shadow-none">
        <CardContent className="grid items-center gap-2 md:grid-cols-2 xl:grid-cols-[minmax(16rem,1fr)_minmax(12rem,0.55fr)_auto_auto]">
          <Input
            aria-label="Filter documents by URL"
            placeholder="Filter by URL"
            value={urlInput}
            onChange={(event) => {
              setUrlInput(event.target.value)
              setOffset(0)
            }}
          />
          <Select
            value={contentType}
            onValueChange={(value) => {
              if (!value) return
              setContentType(value)
              setOffset(0)
            }}
          >
            <SelectTrigger className="w-full">
              <span className="truncate">
                {contentType === "all" ? "All content types" : contentType}
              </span>
            </SelectTrigger>
            <SelectContent align="start">
              <SelectItem value="all">All content types</SelectItem>
              {(mediaTypes.data?.items ?? []).map((mediaType) => (
                <SelectItem key={mediaType} value={mediaType}>
                  {mediaType}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <DateRangeFilter
            value={dateRange}
            onChange={(value) => {
              setDateRange(value)
              setOffset(0)
            }}
          />
          <div className="flex justify-end gap-1">
            <Button
              variant="ghost"
              disabled={activeFilterCount === 0}
              onClick={clearFilters}
            >
              <XIcon />
              Clear
              {activeFilterCount > 0 ? (
                <Badge variant="secondary">{activeFilterCount}</Badge>
              ) : null}
            </Button>
            <Button
              variant="outline"
              size="icon"
              disabled={query.isFetching}
              aria-label="Refresh documents"
              title="Refresh documents"
              onClick={() => void query.refetch()}
            >
              <RefreshCwIcon
                className={query.isFetching ? "animate-spin" : ""}
              />
            </Button>
          </div>
        </CardContent>
      </Card>

      {query.error ? (
        <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive">
          {extractApiError(query.error)}
        </div>
      ) : null}

      <Table containerClassName="min-h-0 flex-1 rounded-md border bg-card/80">
        <TableHeader className="bg-muted/35">
          <TableRow>
            <SortableHead
              label="URL"
              value="url"
              sort={sort}
              direction={direction}
              onSort={updateSort}
            />
            <SortableHead
              label="Content type"
              value="content_type"
              sort={sort}
              direction={direction}
              onSort={updateSort}
            />
            <SortableHead
              label="Representation"
              value="representation"
              sort={sort}
              direction={direction}
              onSort={updateSort}
            />
            <SortableHead
              label="Acquired"
              value="observed_at"
              sort={sort}
              direction={direction}
              onSort={updateSort}
            />
            <SortableHead
              label="Logical size"
              value="content_bytes"
              sort={sort}
              direction={direction}
              onSort={updateSort}
              align="right"
            />
            <SortableHead
              label="Stored size"
              value="stored_bytes"
              sort={sort}
              direction={direction}
              onSort={updateSort}
              align="right"
            />
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {query.isLoading ? (
            <TableRow>
              <TableCell
                colSpan={7}
                className="h-28 text-center text-muted-foreground"
              >
                Loading documents…
              </TableCell>
            </TableRow>
          ) : null}
          {documents.map((document) => (
            <DocumentRow key={document.document_id} document={document} />
          ))}
          {!query.isLoading && !documents.length ? (
            <TableRow>
              <TableCell colSpan={7} className="h-36 text-center">
                <div className="flex flex-col items-center gap-2">
                  <FilesIcon className="size-5 text-muted-foreground" />
                  <span>
                    {query.error
                      ? "Documents could not be loaded"
                      : "No matching documents"}
                  </span>
                  {activeFilterCount > 0 && !query.error ? (
                    <Button variant="outline" size="sm" onClick={clearFilters}>
                      Clear filters
                    </Button>
                  ) : null}
                </div>
              </TableCell>
            </TableRow>
          ) : null}
        </TableBody>
      </Table>

      <ResourcePagination
        total={query.data?.total ?? 0}
        limit={query.data?.limit ?? RESOURCE_PAGE_SIZE}
        offset={query.data?.offset ?? offset}
        isFetching={query.isFetching}
        onOffsetChange={setOffset}
      />
    </div>
  )
}

function SummaryCard({
  icon: Icon,
  label,
  value,
  description,
  loading,
}: {
  icon: LucideIcon
  label: string
  value?: string
  description: string
  loading: boolean
}) {
  return (
    <Card size="sm" className="shadow-none">
      <CardHeader>
        <CardTitle className="text-xs text-muted-foreground">{label}</CardTitle>
        <CardAction>
          <Icon className="size-4 text-muted-foreground" />
        </CardAction>
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="mb-2 h-7 w-24" />
        ) : (
          <p className="text-xl font-semibold tracking-tight tabular-nums">
            {value ?? "—"}
          </p>
        )}
        <CardDescription className="mt-1">{description}</CardDescription>
      </CardContent>
    </Card>
  )
}

function DateRangeFilter({
  value,
  onChange,
}: {
  value?: DateRange
  onChange: (value: DateRange | undefined) => void
}) {
  return (
    <Popover>
      <PopoverTrigger
        render={
          <Button
            variant="outline"
            className="min-w-56 justify-start font-normal"
          />
        }
      >
        <CalendarDaysIcon />
        <span className={value?.from ? "" : "text-muted-foreground"}>
          {dateRangeLabel(value)}
        </span>
      </PopoverTrigger>
      <PopoverContent className="w-auto p-0" align="end">
        <Calendar
          mode="range"
          selected={value}
          defaultMonth={value?.from}
          onSelect={onChange}
          numberOfMonths={2}
        />
        {value?.from ? (
          <div className="flex justify-end border-t p-2">
            <Button variant="ghost" size="sm" onClick={() => onChange(undefined)}>
              Clear dates
            </Button>
          </div>
        ) : null}
      </PopoverContent>
    </Popover>
  )
}

function DocumentRow({ document }: { document: DocumentRecord }) {
  const declaredDiffers =
    document.declared_media_type !== null &&
    document.declared_media_type !== document.detected_media_type

  return (
    <TableRow>
      <TableCell className="max-w-[28rem]">
        <span className="block truncate font-medium" title={document.url}>
          {document.url}
        </span>
        <span
          className="block truncate font-mono text-[0.625rem] text-muted-foreground"
          title={document.content_sha256}
        >
          sha256:{document.content_sha256.slice(0, 16)}…
        </span>
      </TableCell>
      <TableCell>
        <Badge variant="secondary">{document.detected_media_type}</Badge>
        {declaredDiffers ? (
          <span
            className="mt-1 block max-w-52 truncate text-[0.625rem] text-muted-foreground"
            title={`Declared ${document.declared_media_type}`}
          >
            Declared {document.declared_media_type}
          </span>
        ) : null}
      </TableCell>
      <TableCell>{representationLabel(document.representation)}</TableCell>
      <TableCell>{new Date(document.observed_at).toLocaleString()}</TableCell>
      <TableCell className="text-right tabular-nums">
        {formatBytes(document.content_bytes)}
      </TableCell>
      <TableCell className="text-right tabular-nums">
        {formatBytes(document.stored_bytes)}
      </TableCell>
      <TableCell>
        <div className="flex justify-end gap-1">
          <Button
            nativeButton={false}
            render={
              <a
                href={document.url}
                target="_blank"
                rel="noopener noreferrer"
              />
            }
            variant="ghost"
            size="icon-sm"
            aria-label="Open source URL"
            title="Open source URL"
          >
            <ExternalLinkIcon />
          </Button>
          <Button
            nativeButton={false}
            render={
              <a
                href={documentContentUrl(document.document_id)}
                download
              />
            }
            variant="ghost"
            size="icon-sm"
            aria-label="Download owned bytes"
            title="Download owned bytes"
          >
            <DownloadIcon />
          </Button>
        </div>
      </TableCell>
    </TableRow>
  )
}

function SortableHead({
  label,
  value,
  sort,
  direction,
  onSort,
  align = "left",
}: {
  label: string
  value: DocumentSort
  sort: DocumentSort
  direction: SortDirection
  onSort: (value: DocumentSort) => void
  align?: "left" | "right"
}) {
  const active = sort === value
  const Icon = !active
    ? ArrowUpDownIcon
    : direction === "asc"
      ? ArrowUpIcon
      : ArrowDownIcon

  return (
    <TableHead className={align === "right" ? "text-right" : undefined}>
      <button
        type="button"
        className={`inline-flex items-center gap-1 hover:text-foreground ${
          align === "right" ? "ml-auto" : ""
        }`}
        aria-label={`Sort by ${label}`}
        onClick={() => onSort(value)}
      >
        {label}
        <Icon
          className={
            active ? "size-3 text-foreground" : "size-3 text-muted-foreground"
          }
        />
      </button>
    </TableHead>
  )
}

function dateRangeLabel(value?: DateRange) {
  if (!value?.from) return "Any acquisition date"
  if (!value.to) return `From ${format(value.from, "MMM d, yyyy")}`
  return `${format(value.from, "MMM d, yyyy")} – ${format(
    value.to,
    "MMM d, yyyy"
  )}`
}

function startOfLocalDay(value?: Date) {
  if (!value) return undefined
  return new Date(
    value.getFullYear(),
    value.getMonth(),
    value.getDate()
  ).toISOString()
}

function startOfNextLocalDay(value?: Date) {
  if (!value) return undefined
  return new Date(
    value.getFullYear(),
    value.getMonth(),
    value.getDate() + 1
  ).toISOString()
}

function representationLabel(value: DocumentRecord["representation"]) {
  return value === "rendered_html" ? "Rendered HTML" : "Response body"
}

function formatBytes(bytes: number) {
  if (bytes < 1_024) return `${bytes.toLocaleString()} B`
  if (bytes < 1_048_576) return `${(bytes / 1_024).toFixed(1)} KB`
  if (bytes < 1_073_741_824) return `${(bytes / 1_048_576).toFixed(1)} MB`
  return `${(bytes / 1_073_741_824).toFixed(1)} GB`
}
