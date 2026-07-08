import { useMemo, useState } from "react"
import {
  ExternalLinkIcon,
  FileArchiveIcon,
  FilterIcon,
  SearchIcon,
  RefreshCwIcon,
  ShieldOffIcon,
} from "lucide-react"

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
import {
  useArtifacts,
  useInvalidateArtifacts,
} from "@/hooks/use-history-data"
import { HistoryPagination, HISTORY_PAGE_SIZE } from "@/pages/history/history-pagination"
import type {
  ArtifactFilters,
  ArtifactKind,
  ArtifactRecord,
} from "@/types/history"

const artifactKinds: Array<"all" | ArtifactKind> = [
  "all",
  "html",
  "screenshot",
  "pdf",
  "mhtml",
]

const defaultFilters: ArtifactFilters = {
  urlPattern: "",
  kind: "all",
  invalidated: "active",
  warnings: "all",
}

export function ArtifactsPage() {
  const [filters, setFilters] = useState<ArtifactFilters>(() => ({
    ...defaultFilters,
    urlPattern: new URLSearchParams(window.location.search).get("url") ?? "",
  }))
  const [offset, setOffset] = useState(0)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [confirmMode, setConfirmMode] = useState<"selected" | "filtered" | null>(null)
  const artifactsQuery = useArtifacts(filters, {
    limit: HISTORY_PAGE_SIZE,
    offset,
  })
  const invalidateMutation = useInvalidateArtifacts()
  const artifacts = artifactsQuery.data?.items ?? []
  const total = artifactsQuery.data?.total ?? 0
  const selectedArtifacts = useMemo(() => {
    return artifacts.filter((artifact) => selectedIds.has(artifact.id))
  }, [artifacts, selectedIds])
  const allVisibleSelected =
    artifacts.length > 0 && artifacts.every((artifact) => selectedIds.has(artifact.id))

  const patchFilters = (patch: Partial<ArtifactFilters>) => {
    setSelectedIds(new Set())
    setOffset(0)
    setFilters((current) => ({ ...current, ...patch }))
  }

  const invalidateSelected = () => {
    invalidateMutation.mutate(
      {
        artifact_ids: selectedArtifacts.map((artifact) => artifact.id),
        reason: "manual-ui-selection",
      },
      {
        onSuccess: () => {
          setSelectedIds(new Set())
          setConfirmMode(null)
        },
      }
    )
  }

  const invalidateFiltered = () => {
    invalidateMutation.mutate(
      {
        url_pattern: filters.urlPattern.trim() || undefined,
        kind: filters.kind === "all" ? undefined : filters.kind,
        warnings:
          filters.warnings === "all" ? undefined : filters.warnings === "warning",
        reason: "manual-ui-filter",
      },
      {
        onSuccess: () => {
          setSelectedIds(new Set())
          setConfirmMode(null)
        },
      }
    )
  }

  return (
    <div className="flex min-h-0 w-full flex-col gap-4">
      <section className="flex flex-col gap-3 border-b pb-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2">
            <FileArchiveIcon className="size-4 text-muted-foreground" />
            <h1 className="truncate text-lg font-medium">Artifacts</h1>
            <Badge variant="outline">{total}</Badge>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={artifactsQuery.isFetching}
              onClick={() => void artifactsQuery.refetch()}
            >
              <RefreshCwIcon />
              Refresh
            </Button>
            <Button
              variant="secondary"
              size="sm"
              disabled={selectedArtifacts.length === 0}
              onClick={() => setConfirmMode("selected")}
            >
              <ShieldOffIcon />
              Invalidate selected
            </Button>
            <Button
              variant="destructive"
              size="sm"
              disabled={artifacts.length === 0}
              onClick={() => setConfirmMode("filtered")}
            >
              <ShieldOffIcon />
              Invalidate filtered
            </Button>
          </div>
        </div>
        <div className="grid gap-2 lg:grid-cols-[minmax(18rem,1fr)_10rem_10rem_10rem]">
          <div className="relative">
            <FilterIcon className="pointer-events-none absolute top-1/2 left-3 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="pl-9"
              value={filters.urlPattern}
              placeholder="*example.com/item/*"
              onChange={(event) => patchFilters({ urlPattern: event.target.value })}
            />
          </div>
          <FilterSelect
            value={filters.kind}
            options={artifactKinds.map((kind) => ({ value: kind, label: kind }))}
            onChange={(kind) => patchFilters({ kind: kind as ArtifactFilters["kind"] })}
            aria-label="Artifact kind"
          />
          <FilterSelect
            value={filters.invalidated}
            options={[
              { value: "all", label: "All cache" },
              { value: "active", label: "Active" },
              { value: "invalidated", label: "Invalidated" },
            ]}
            onChange={(invalidated) =>
              patchFilters({ invalidated: invalidated as ArtifactFilters["invalidated"] })
            }
            aria-label="Cache state"
          />
          <FilterSelect
            value={filters.warnings}
            options={[
              { value: "all", label: "All warnings" },
              { value: "clean", label: "Clean" },
              { value: "warning", label: "Warnings" },
            ]}
            onChange={(warnings) =>
              patchFilters({ warnings: warnings as ArtifactFilters["warnings"] })
            }
            aria-label="Warning state"
          />
        </div>
      </section>

      <Table containerClassName="min-h-0 flex-1 rounded-md border bg-card/80">
        <TableHeader>
          <TableRow>
            <TableHead className="w-9">
              <input
                type="checkbox"
                className="size-3.5 accent-primary"
                checked={allVisibleSelected}
                onChange={(event) => {
                  setSelectedIds(
                    event.target.checked
                      ? new Set(artifacts.map((artifact) => artifact.id))
                      : new Set()
                  )
                }}
                aria-label="Select visible artifacts"
              />
            </TableHead>
            <TableHead>Kind</TableHead>
            <TableHead>URL</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Warnings</TableHead>
            <TableHead>Size</TableHead>
            <TableHead>Created</TableHead>
            <TableHead className="w-20" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {artifacts.map((artifact) => (
            <ArtifactRow
              key={artifact.id}
              artifact={artifact}
              selected={selectedIds.has(artifact.id)}
              onSelectedChange={(selected) => {
                setSelectedIds((current) => {
                  const next = new Set(current)
                  if (selected) {
                    next.add(artifact.id)
                  } else {
                    next.delete(artifact.id)
                  }
                  return next
                })
              }}
            />
          ))}
          {!artifactsQuery.isLoading && artifacts.length === 0 ? (
            <TableRow>
              <TableCell colSpan={8} className="h-24 text-center text-muted-foreground">
                No artifacts match.
              </TableCell>
            </TableRow>
          ) : null}
        </TableBody>
      </Table>

      <HistoryPagination
        total={total}
        limit={artifactsQuery.data?.limit ?? HISTORY_PAGE_SIZE}
        offset={artifactsQuery.data?.offset ?? offset}
        isFetching={artifactsQuery.isFetching}
        onOffsetChange={(nextOffset) => {
          setSelectedIds(new Set())
          setOffset(nextOffset)
        }}
      />

      <InvalidateDialog
        mode={confirmMode}
        count={confirmMode === "selected" ? selectedArtifacts.length : total}
        filters={filters}
        pending={invalidateMutation.isPending}
        onOpenChange={(open) => {
          if (!open) {
            setConfirmMode(null)
          }
        }}
        onConfirm={confirmMode === "selected" ? invalidateSelected : invalidateFiltered}
      />
    </div>
  )
}

function ArtifactRow({
  artifact,
  selected,
  onSelectedChange,
}: {
  artifact: ArtifactRecord
  selected: boolean
  onSelectedChange: (selected: boolean) => void
}) {
  const warnings = artifact.warning_count
  const detailHref = `/history/artifacts/${artifact.id}`

  return (
    <TableRow data-state={selected ? "selected" : undefined}>
      <TableCell>
        <input
          type="checkbox"
          className="size-3.5 accent-primary"
          checked={selected}
          onChange={(event) => onSelectedChange(event.target.checked)}
          aria-label={`Select ${artifact.kind} artifact`}
        />
      </TableCell>
      <TableCell>
        <Badge variant="outline">{artifact.kind}</Badge>
      </TableCell>
      <TableCell className="max-w-[34rem]">
        <a
          href={detailHref}
          className="block min-w-0 rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
        >
          <span className="block truncate font-medium text-primary underline-offset-4 hover:underline">
            {artifact.normalized_url ?? "No URL"}
          </span>
          <span className="block truncate text-muted-foreground">
            {artifact.path_name ?? artifact.path}
          </span>
        </a>
      </TableCell>
      <TableCell>
        <Badge variant={artifact.invalidated_at ? "destructive" : "secondary"}>
          {artifact.invalidated_at ? "Invalidated" : "Active"}
        </Badge>
      </TableCell>
      <TableCell>
        <Badge variant={warnings > 0 ? "destructive" : "outline"}>{warnings}</Badge>
      </TableCell>
      <TableCell>{formatBytes(artifact.size_bytes)}</TableCell>
      <TableCell>{formatDate(artifact.created_at)}</TableCell>
      <TableCell className="text-right">
        <Button
          variant="ghost"
          size="icon-sm"
          nativeButton={false}
          render={<a href={detailHref} />}
          aria-label={`Inspect ${artifact.kind} artifact`}
        >
          <SearchIcon />
        </Button>
        {artifact.normalized_url ? (
          <Button
            variant="ghost"
            size="icon-sm"
            nativeButton={false}
            render={<a href={artifact.normalized_url} target="_blank" rel="noreferrer" />}
          >
            <ExternalLinkIcon />
          </Button>
        ) : null}
      </TableCell>
    </TableRow>
  )
}

function InvalidateDialog({
  mode,
  count,
  filters,
  pending,
  onOpenChange,
  onConfirm,
}: {
  mode: "selected" | "filtered" | null
  count: number
  filters: ArtifactFilters
  pending: boolean
  onOpenChange: (open: boolean) => void
  onConfirm: () => void
}) {
  return (
    <Dialog open={mode !== null} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Invalidate cache</DialogTitle>
          <DialogDescription>
            {mode === "selected"
              ? `${count} selected artifacts will be invalidated.`
              : `${count} visible artifacts match ${filterSummary(filters)}.`}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" disabled={pending} onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="destructive" disabled={pending || count === 0} onClick={onConfirm}>
            <ShieldOffIcon />
            Invalidate
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
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

function filterSummary(filters: ArtifactFilters) {
  const parts = [
    filters.urlPattern.trim() || "*",
    filters.kind,
    filters.invalidated,
    filters.warnings,
  ].filter((part) => part !== "all")

  return parts.join(" / ")
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
    return ""
  }

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value))
}
