import { useState } from "react"
import {
  ArrowLeftIcon,
  DatabaseZapIcon,
  ExternalLinkIcon,
  PauseIcon,
  PlayIcon,
  RefreshCwIcon,
  Trash2Icon,
} from "lucide-react"

import { CatalogueEmptyState, CataloguePanel } from "@/components/catalogue/catalogue-workspace"
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
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import {
  useDematerialize,
  useCatalogueMaterialization,
  useRebuildCatalogueMaterialization,
  useUpdateCatalogueMaterializationMaintenance,
} from "@/hooks/use-catalogue-materializations"
import type { CatalogueMaterializationRecord } from "@/types/catalogue"

export function CatalogueMaterializationPage({ materializationId }: { materializationId?: string }) {
  const query = useCatalogueMaterialization(materializationId)
  const selected = query.data
  if (!selected) {
    return <CataloguePanel><CatalogueEmptyState icon={DatabaseZapIcon} title={query.isLoading ? "Loading durable data…" : "Materialization not found"} description={query.isLoading ? "Fetching storage, schema, and maintenance state." : "This materialization may have been removed."} action={!query.isLoading ? <Button nativeButton={false} render={<a href="/catalogue/queries" />}>Back to catalogue</Button> : undefined} className="min-h-[32rem]" /></CataloguePanel>
  }
  const sourceHref = selected.query_id ? `/catalogue/queries/${selected.query_id}` : selected.view_reference_id ? `/catalogue/views/${selected.view_reference_id}` : "/catalogue/views"
  return <div className="flex min-h-0 w-full flex-col gap-4 overflow-y-auto"><div><Button nativeButton={false} render={<a href={sourceHref} />} variant="ghost"><ArrowLeftIcon />Back to source definition</Button></div><CatalogueMaterializationDetail key={selected.id} materialization={selected} /></div>
}

export function CatalogueMaterializationDetail({ materialization }: { materialization: CatalogueMaterializationRecord }) {
  const refresh = useRebuildCatalogueMaterialization()
  const maintenance = useUpdateCatalogueMaterializationMaintenance()
  const [rate, setRate] = useState(materialization.backfill_scopes_per_minute)
  const [dematerializeOpen, setDematerializeOpen] = useState(false)

  const incremental = materialization.refresh_mode === "scope_incremental"
  const complete = materialization.completed_scopes ?? 0
  const total = materialization.total_scopes ?? 0
  const progress = total ? Math.min(100, (complete / total) * 100) : 0

  return (
    <CataloguePanel className="min-h-0 overflow-y-auto">
      <div className="flex flex-wrap items-start justify-between gap-4 border-b bg-gradient-to-r from-primary/5 to-transparent p-5">
        <div className="flex items-start gap-3">
          <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary"><DatabaseZapIcon className="size-4" /></span>
          <div>
          <div className="flex items-center gap-2">
            <h2 className="text-base font-semibold">Durable data</h2>
            <StatusBadge materialization={materialization} />
          </div>
          <p className="mt-1 max-w-2xl font-mono text-[10px] text-muted-foreground">{materialization.qualified_name} · {materialization.row_count.toLocaleString()} rows</p>
          {materialization.description && <p className="mt-1 max-w-2xl text-xs text-muted-foreground">{materialization.description}</p>}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button nativeButton={false} render={<a href={`/catalogue/materializations/${materialization.id}`} />} size="sm" variant="ghost"><ExternalLinkIcon />Open standalone</Button>
          {!incremental && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => refresh.mutate({ materialization })}
              disabled={refresh.isPending || materialization.status === "dematerializing"}
            >
              <RefreshCwIcon />Full refresh
            </Button>
          )}
          <Button
            size="sm"
            variant="destructive"
            onClick={() => setDematerializeOpen(true)}
            disabled={materialization.status === "dematerializing"}
          >
            <Trash2Icon />Dematerialize
          </Button>
        </div>
      </div>
      <div className="grid gap-5 p-5 xl:grid-cols-[minmax(0,1fr)_18rem]">
        <div className="grid gap-5">
          {incremental && (
            <div className="grid gap-3 rounded-2xl border bg-muted/10 p-4 shadow-sm">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-medium">Live maintenance</div>
                  <div className="text-xs text-muted-foreground">
                    Pausing preserves the CDC cursor; resume catches up from the same position.
                  </div>
                </div>
                <Button
                  size="sm"
                  variant={materialization.live_enabled ? "outline" : "default"}
                  onClick={() =>
                    maintenance.mutate({ id: materialization.id, live_enabled: !materialization.live_enabled })
                  }
                  disabled={maintenance.isPending || materialization.status === "dematerializing"}
                >
                  {materialization.live_enabled ? <PauseIcon /> : <PlayIcon />}
                  {materialization.live_enabled ? "Pause live" : "Resume live"}
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
                  variant={materialization.backfill_enabled ? "outline" : "default"}
                  onClick={() =>
                    maintenance.mutate({ id: materialization.id, backfill_enabled: !materialization.backfill_enabled })
                  }
                  disabled={maintenance.isPending || materialization.status === "dematerializing"}
                >
                  {materialization.backfill_enabled ? <PauseIcon /> : <PlayIcon />}
                  {materialization.backfill_enabled ? "Pause backfill" : "Resume backfill"}
                </Button>
              </div>
              <Progress value={progress}>
                <ProgressLabel>Activation coverage</ProgressLabel>
                <ProgressValue>{() => `${progress.toFixed(0)}%`}</ProgressValue>
              </Progress>
              <div className="flex flex-wrap items-end gap-2 border-t pt-3">
                <div className="grid gap-1.5">
                  <Label htmlFor={`rate-${materialization.id}`}>Scopes per minute</Label>
                  <Input
                    id={`rate-${materialization.id}`}
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
                    maintenance.mutate({ id: materialization.id, backfill_scopes_per_minute: rate })
                  }
                  disabled={maintenance.isPending || rate === materialization.backfill_scopes_per_minute}
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
              {materialization.columns.map((column) => (
                <Badge key={column.name} variant="outline" className="font-mono">
                  {column.name} · {column.data_type}
                </Badge>
              ))}
            </div>
          </div>
        </div>
        <dl className="grid content-start gap-3 rounded-2xl border bg-muted/20 p-4 text-xs shadow-inner">
          <Fact label="Source" value={sourceLabel(materialization)} />
          <Fact label="Rows" value={materialization.row_count.toLocaleString()} />
          <Fact label="Active storage" value={formatBytes(materialization.active_storage_bytes)} />
          <Fact label="Active files" value={materialization.active_file_count.toLocaleString()} />
          <Fact label="Partitioning" value={materialization.partitioning.join(", ") || "Unpartitioned"} />
          <Fact label="Activation snapshot" value={materialization.activation_snapshot?.toLocaleString() ?? "Not applicable"} />
          <Fact label="Last scope" value={materialization.last_scope_completed_at ? new Date(materialization.last_scope_completed_at).toLocaleString() : "Not applicable"} />
          <Fact label="Failed scopes" value={materialization.failed_scopes.toLocaleString()} />
        </dl>
      </div>
      <DematerializeDialog materialization={materialization} open={dematerializeOpen} onOpenChange={setDematerializeOpen} />
    </CataloguePanel>
  )
}

function DematerializeDialog({
  materialization,
  open,
  onOpenChange,
}: {
  materialization: CatalogueMaterializationRecord
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [confirmation, setConfirmation] = useState("")
  const dematerialize = useDematerialize()

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => { if (!nextOpen) setConfirmation(""); onOpenChange(nextOpen) }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Dematerialize {materialization.qualified_name}?</DialogTitle>
          <DialogDescription>
            Atlas will stop live discovery and backfill, fence queued commits, then let the repository
            worker drop the DuckLake table. The source query or view remains available. Historical snapshots may retain physical files until the
            configured retention window expires.
          </DialogDescription>
        </DialogHeader>
        <div className="rounded-lg border bg-muted/20 p-3 text-sm">
          <div>{materialization.row_count.toLocaleString()} rows</div>
          <div>{formatBytes(materialization.active_storage_bytes)} across {materialization.active_file_count.toLocaleString()} active files</div>
        </div>
        <div className="grid gap-1.5">
          <Label>Type {materialization.name} to confirm</Label>
          <Input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} />
        </div>
        <DialogFooter showCloseButton>
          <Button
            variant="destructive"
            disabled={confirmation !== materialization.name || dematerialize.isPending}
            onClick={() =>
              dematerialize.mutate(materialization, { onSuccess: () => onOpenChange(false) })
            }
          >
            {dematerialize.isPending ? "Stopping work…" : "Stop work and dematerialize"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function StatusBadge({ materialization }: { materialization: CatalogueMaterializationRecord }) {
  const labels: Record<CatalogueMaterializationRecord["status"], string> = {
    full_refresh: "Full refresh",
    live: "Live",
    backfilling: "Backfilling",
    paused: "Paused",
    dematerializing: "Dematerializing",
    degraded: "Needs attention",
    source_changing: "Changing source",
    source_changed: "Source changed",
  }
  const explanation =
    materialization.status === "paused"
      ? "Existing data remains queryable. Resume live or backfill maintenance from the detail panel."
      : materialization.status === "dematerializing"
        ? "New work is fenced while the repository worker removes the table."
        : materialization.status === "source_changing"
          ? "The source view edit is incomplete. Maintenance remains fenced until recovery."
          : materialization.status === "source_changed"
            ? "The source view changed. Rebuild or dematerialize before maintenance can resume."
        : `${labels[materialization.status]} materialization`
  return (
    <Tooltip>
      <TooltipTrigger
        render={<Badge variant={materialization.status === "degraded" || materialization.status === "dematerializing" || materialization.status === "source_changing" || materialization.status === "source_changed" ? "destructive" : materialization.status === "live" || materialization.status === "backfilling" ? "default" : "secondary"} />}
      >
        {labels[materialization.status]}
      </TooltipTrigger>
      <TooltipContent>{explanation}</TooltipContent>
    </Tooltip>
  )
}

function Fact({ label, value }: { label: string; value: string }) {
  return <div><dt className="text-muted-foreground">{label}</dt><dd className="mt-0.5 break-words font-medium">{value}</dd></div>
}

function sourceLabel(materialization: CatalogueMaterializationRecord): string {
  if (materialization.query_name) return `${materialization.query_name} · v${materialization.query_revision}`
  return materialization.view_name ?? "Unavailable source"
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`
  return `${(value / 1024 ** 3).toFixed(1)} GB`
}
