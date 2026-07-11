import { useEffect, useState } from "react"
import {
  DatabaseZapIcon,
  PauseIcon,
  PlayIcon,
  RefreshCwIcon,
  Trash2Icon,
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
import { Label } from "@/components/ui/label"
import { Progress, ProgressLabel, ProgressValue } from "@/components/ui/progress"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import {
  useDropMaterializedView,
  useMaterializedViews,
  useRefreshMaterializedView,
  useUpdateMaterializedViewMaintenance,
} from "@/hooks/use-materialized-views"
import type { MaterializedViewRecord } from "@/types/catalogue"

export function MaterializedViewsPage() {
  const query = useMaterializedViews()
  const views = query.data?.items ?? []
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selected = views.find((view) => view.id === selectedId) ?? views[0] ?? null

  useEffect(() => {
    if (selected && selected.id !== selectedId) setSelectedId(selected.id)
  }, [selected, selectedId])

  return (
    <div className="flex min-h-0 w-full flex-col gap-4 overflow-y-auto">
      <div className="flex items-center gap-2">
        <DatabaseZapIcon className="size-4 text-primary" />
        <h1 className="text-lg font-medium">Materialized Views</h1>
        <Badge variant="outline">{views.length}</Badge>
      </div>
      <Table containerClassName="shrink-0 rounded-xl border bg-card/80">
        <TableHeader>
          <TableRow>
            <TableHead>Table</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Source</TableHead>
            <TableHead>Rows</TableHead>
            <TableHead>Storage</TableHead>
            <TableHead>Files</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {views.map((view) => (
            <TableRow
              key={view.id}
              className={`cursor-pointer ${selected?.id === view.id ? "bg-primary/5" : ""}`}
              onClick={() => setSelectedId(view.id)}
            >
              <TableCell>
                <div className="font-medium">{view.display_name}</div>
                <div className="font-mono text-[10px] text-muted-foreground">
                  {view.qualified_name}
                </div>
              </TableCell>
              <TableCell><StatusBadge view={view} /></TableCell>
              <TableCell>{sourceLabel(view)}</TableCell>
              <TableCell>{view.row_count.toLocaleString()}</TableCell>
              <TableCell>{formatBytes(view.active_storage_bytes)}</TableCell>
              <TableCell>{view.active_file_count.toLocaleString()}</TableCell>
            </TableRow>
          ))}
          {!query.isLoading && views.length === 0 && (
            <TableRow>
              <TableCell colSpan={6} className="h-28 text-center text-muted-foreground">
                Materialize SQL, a saved-query revision, or a view to create one.
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
      {selected && <MaterializedViewDetail view={selected} />}
    </div>
  )
}

function MaterializedViewDetail({ view }: { view: MaterializedViewRecord }) {
  const refresh = useRefreshMaterializedView()
  const maintenance = useUpdateMaterializedViewMaintenance()
  const [rate, setRate] = useState(view.backfill_scopes_per_minute)
  const [deleteOpen, setDeleteOpen] = useState(false)

  useEffect(() => setRate(view.backfill_scopes_per_minute), [view.backfill_scopes_per_minute])

  const incremental = view.refresh_mode === "scope_incremental"
  const complete = view.completed_scopes ?? 0
  const total = view.total_scopes ?? 0
  const progress = total ? Math.min(100, (complete / total) * 100) : 0

  return (
    <section className="rounded-xl border bg-card/80">
      <div className="flex flex-wrap items-start justify-between gap-4 border-b p-4">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="font-medium">{view.display_name}</h2>
            <StatusBadge view={view} />
          </div>
          <p className="mt-1 max-w-2xl text-xs text-muted-foreground">
            {view.description || "No description"}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {!incremental && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => refresh.mutate(view)}
              disabled={refresh.isPending || view.status === "deleting"}
            >
              <RefreshCwIcon />Full refresh
            </Button>
          )}
          <Button
            size="sm"
            variant="destructive"
            onClick={() => setDeleteOpen(true)}
            disabled={view.status === "deleting"}
          >
            <Trash2Icon />Delete data
          </Button>
        </div>
      </div>
      <div className="grid gap-5 p-4 lg:grid-cols-[minmax(0,1fr)_18rem]">
        <div className="grid gap-5">
          {incremental && (
            <div className="grid gap-3 rounded-lg border p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-medium">Live maintenance</div>
                  <div className="text-xs text-muted-foreground">
                    Pausing preserves the CDC cursor; resume catches up from the same position.
                  </div>
                </div>
                <Button
                  size="sm"
                  variant={view.live_enabled ? "outline" : "default"}
                  onClick={() =>
                    maintenance.mutate({ id: view.id, live_enabled: !view.live_enabled })
                  }
                  disabled={maintenance.isPending || view.status === "deleting"}
                >
                  {view.live_enabled ? <PauseIcon /> : <PlayIcon />}
                  {view.live_enabled ? "Pause live" : "Resume live"}
                </Button>
              </div>
              <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-3">
                <div>
                  <div className="text-sm font-medium">Historical backfill</div>
                  <div className="text-xs text-muted-foreground">
                    {total ? `${complete.toLocaleString()} of ${total.toLocaleString()} activation scopes covered.` : "Waiting for scope totals."}
                  </div>
                </div>
                <Button
                  size="sm"
                  variant={view.backfill_enabled ? "outline" : "default"}
                  onClick={() =>
                    maintenance.mutate({ id: view.id, backfill_enabled: !view.backfill_enabled })
                  }
                  disabled={maintenance.isPending || view.status === "deleting"}
                >
                  {view.backfill_enabled ? <PauseIcon /> : <PlayIcon />}
                  {view.backfill_enabled ? "Pause backfill" : "Resume backfill"}
                </Button>
              </div>
              <Progress value={progress}>
                <ProgressLabel>Activation coverage</ProgressLabel>
                <ProgressValue>{() => `${progress.toFixed(0)}%`}</ProgressValue>
              </Progress>
              <div className="flex flex-wrap items-end gap-2 border-t pt-3">
                <div className="grid gap-1.5">
                  <Label htmlFor={`rate-${view.id}`}>Scopes per minute</Label>
                  <Input
                    id={`rate-${view.id}`}
                    className="w-36"
                    type="number"
                    min={1}
                    max={10_000}
                    value={rate}
                    onChange={(event) => setRate(Number(event.target.value) || 1)}
                  />
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() =>
                    maintenance.mutate({ id: view.id, backfill_scopes_per_minute: rate })
                  }
                  disabled={maintenance.isPending || rate === view.backfill_scopes_per_minute}
                >
                  Save rate
                </Button>
              </div>
            </div>
          )}
          <div>
            <div className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Columns
            </div>
            <div className="flex flex-wrap gap-2">
              {view.columns.map((column) => (
                <Badge key={column.name} variant="outline" className="font-mono">
                  {column.name} · {column.data_type}
                </Badge>
              ))}
            </div>
          </div>
        </div>
        <dl className="grid content-start gap-3 rounded-lg border bg-muted/20 p-4 text-xs">
          <Fact label="Source" value={sourceLabel(view)} />
          <Fact label="Rows" value={view.row_count.toLocaleString()} />
          <Fact label="Active storage" value={formatBytes(view.active_storage_bytes)} />
          <Fact label="Active files" value={view.active_file_count.toLocaleString()} />
          <Fact label="Partitioning" value={view.partitioning.join(", ") || "Unpartitioned"} />
          <Fact label="Activation snapshot" value={view.activation_snapshot?.toLocaleString() ?? "Not applicable"} />
          <Fact label="Last scope" value={view.last_scope_completed_at ? new Date(view.last_scope_completed_at).toLocaleString() : "Not applicable"} />
          <Fact label="Failed scopes" value={view.failed_scopes.toLocaleString()} />
        </dl>
      </div>
      <DeleteMaterializedViewDialog view={view} open={deleteOpen} onOpenChange={setDeleteOpen} />
    </section>
  )
}

function DeleteMaterializedViewDialog({
  view,
  open,
  onOpenChange,
}: {
  view: MaterializedViewRecord
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [confirmation, setConfirmation] = useState("")
  const drop = useDropMaterializedView()

  useEffect(() => {
    if (open) setConfirmation("")
  }, [open])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete {view.qualified_name}?</DialogTitle>
          <DialogDescription>
            Atlas will stop live discovery and backfill, fence queued commits, then let the repository
            worker drop the DuckLake table. Historical snapshots may retain physical files until the
            configured retention window expires.
          </DialogDescription>
        </DialogHeader>
        <div className="rounded-lg border bg-muted/20 p-3 text-sm">
          <div>{view.row_count.toLocaleString()} rows</div>
          <div>{formatBytes(view.active_storage_bytes)} across {view.active_file_count.toLocaleString()} active files</div>
        </div>
        <div className="grid gap-1.5">
          <Label>Type {view.name} to confirm</Label>
          <Input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} />
        </div>
        <DialogFooter showCloseButton>
          <Button
            variant="destructive"
            disabled={confirmation !== view.name || drop.isPending}
            onClick={() =>
              drop.mutate(view, { onSuccess: () => onOpenChange(false) })
            }
          >
            {drop.isPending ? "Stopping work…" : "Stop work and delete data"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function StatusBadge({ view }: { view: MaterializedViewRecord }) {
  const labels: Record<MaterializedViewRecord["status"], string> = {
    full_refresh: "Full refresh",
    live: "Live",
    backfilling: "Backfilling",
    paused: "Paused",
    deleting: "Deleting",
    degraded: "Needs attention",
  }
  const explanation =
    view.status === "paused"
      ? "Existing data remains queryable. Resume live or backfill maintenance from the detail panel."
      : view.status === "deleting"
        ? "New work is fenced while the repository worker removes the table."
        : `${labels[view.status]} materialized view`
  return (
    <Tooltip>
      <TooltipTrigger
        render={<Badge variant={view.status === "degraded" || view.status === "deleting" ? "destructive" : view.status === "live" || view.status === "backfilling" ? "default" : "secondary"} />}
      >
        {labels[view.status]}
      </TooltipTrigger>
      <TooltipContent>{explanation}</TooltipContent>
    </Tooltip>
  )
}

function Fact({ label, value }: { label: string; value: string }) {
  return <div><dt className="text-muted-foreground">{label}</dt><dd className="mt-0.5 break-words font-medium">{value}</dd></div>
}

function sourceLabel(view: MaterializedViewRecord): string {
  if (view.query_name) return `${view.query_name} · v${view.query_revision}`
  return view.source_view_name ?? "Unavailable source"
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`
  return `${(value / 1024 ** 3).toFixed(1)} GB`
}
