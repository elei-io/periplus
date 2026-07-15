import { useState } from "react"
import { PauseIcon, PlayIcon, RefreshCwIcon, Trash2Icon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useDematerialize, useRebuildCatalogueMaterialization, useUpdateCatalogueMaterializationMaintenance } from "@/hooks/use-catalogue-materializations"
import type { CatalogueMaterializationRecord } from "@/types/catalogue"

export function CatalogueMaterializationDetail({ materialization }: { materialization: CatalogueMaterializationRecord }) {
  const maintenance = useUpdateCatalogueMaterializationMaintenance()
  const rebuild = useRebuildCatalogueMaterialization()
  const [rate, setRate] = useState(materialization.backfill_scopes_per_minute)
  const [dematerializeOpen, setDematerializeOpen] = useState(false)
  const complete = materialization.completed_scopes ?? 0
  const total = materialization.total_scopes ?? 0

  return <>
    <div className="grid gap-6 p-5">
      <section>
        <div className="mb-3 flex items-baseline justify-between gap-3"><h3 className="text-sm font-medium">Materialization backlog</h3><span className="text-[10px] text-muted-foreground">This view only</span></div>
        <div className="grid gap-px overflow-hidden rounded-xl bg-border sm:grid-cols-3">
          <BacklogFact label="Live updates" value={materialization.pending_live_scopes} detail="planned scopes not yet settled" />
          <BacklogFact label="Existing data" value={materialization.remaining_backfill_scopes} detail="historical scopes remaining" />
          <BacklogFact label="Failed" value={materialization.failed_scopes} detail="scopes requiring attention" failed={materialization.failed_scopes > 0} />
        </div>
      </section>

      <section>
        <h3 className="mb-1 text-sm font-medium">Live updates</h3>
        <div className="divide-y">
          <div className="flex flex-wrap items-center justify-between gap-3 py-3"><div><div className="text-sm">Incremental discriminator</div><div className="text-xs text-muted-foreground">{materialization.scope_kind === "document" ? "Document" : "Crawl"} · <code>{materialization.scope_column}</code></div></div></div>
          <div className="flex flex-wrap items-center justify-between gap-3 py-3"><div><div className="text-sm">New data</div><div className="text-xs text-muted-foreground">Process matching crawl data as it arrives</div></div><Button size="sm" variant={materialization.live_enabled ? "outline" : "default"} onClick={() => maintenance.mutate({ id: materialization.id, live_enabled: !materialization.live_enabled })} disabled={maintenance.isPending || materialization.status === "dematerializing"}>{materialization.live_enabled ? <PauseIcon /> : <PlayIcon />}{materialization.live_enabled ? "Pause" : "Resume"}</Button></div>
          <div className="flex flex-wrap items-center justify-between gap-3 py-3"><div><div className="text-sm">Existing data</div><div className="text-xs text-muted-foreground">{total ? `${complete.toLocaleString()} of ${total.toLocaleString()} ${materialization.scope_kind}s complete` : "Waiting for scope totals"}</div></div><div className="flex items-center gap-2"><Input aria-label="Backfill units per minute" className="h-8 w-24" type="number" min={1} max={10_000} value={rate} onChange={(event) => setRate(Number(event.target.value) || 1)} /><span className="text-xs text-muted-foreground">/ min</span><Button size="sm" variant="outline" onClick={() => maintenance.mutate({ id: materialization.id, backfill_scopes_per_minute: rate })} disabled={maintenance.isPending || rate === materialization.backfill_scopes_per_minute}>Save</Button><Button size="sm" variant={materialization.backfill_enabled ? "outline" : "default"} onClick={() => maintenance.mutate({ id: materialization.id, backfill_enabled: !materialization.backfill_enabled })} disabled={maintenance.isPending || materialization.status === "dematerializing"}>{materialization.backfill_enabled ? <PauseIcon /> : <PlayIcon />}{materialization.backfill_enabled ? "Pause" : "Resume"}</Button></div></div>
        </div>
      </section>

      <section><h3 className="mb-3 text-sm font-medium">Stored results</h3><dl className="grid gap-4 text-xs sm:grid-cols-2 lg:grid-cols-4"><Fact label="Rows" value={materialization.row_count.toLocaleString()} /><Fact label="Size" value={formatBytes(materialization.active_storage_bytes)} /><Fact label="Files" value={materialization.active_file_count.toLocaleString()} /><Fact label="Last update settled" value={materialization.last_scope_completed_at ? new Date(materialization.last_scope_completed_at).toLocaleString() : "No updates yet"} /></dl></section>

      <div className="flex flex-wrap justify-end gap-2 border-t pt-4"><Button size="sm" variant="outline" onClick={() => rebuild.mutate({ materialization })} disabled={rebuild.isPending || materialization.status === "dematerializing"}><RefreshCwIcon />{rebuild.isPending ? "Starting…" : "Rebuild"}</Button><Button size="sm" variant="destructive" onClick={() => setDematerializeOpen(true)} disabled={materialization.status === "dematerializing"}><Trash2Icon />Turn off materialization</Button></div>
    </div>
    <DematerializeDialog materialization={materialization} open={dematerializeOpen} onOpenChange={setDematerializeOpen} />
  </>
}

function DematerializeDialog({ materialization, open, onOpenChange }: { materialization: CatalogueMaterializationRecord; open: boolean; onOpenChange: (open: boolean) => void }) {
  const [confirmation, setConfirmation] = useState("")
  const dematerialize = useDematerialize()
  return <Dialog open={open} onOpenChange={(nextOpen) => { if (!nextOpen) setConfirmation(""); onOpenChange(nextOpen) }}><DialogContent><DialogHeader><DialogTitle>Turn off materialization for {materialization.display_name}?</DialogTitle><DialogDescription>The view remains available, but reads will evaluate its SQL directly. Atlas will stop live updates and remove the stored results.</DialogDescription></DialogHeader><div className="rounded-lg border bg-muted/20 p-3 text-sm"><div>{materialization.row_count.toLocaleString()} rows</div><div>{formatBytes(materialization.active_storage_bytes)} across {materialization.active_file_count.toLocaleString()} active files</div></div><div className="grid gap-1.5"><Label>Type {materialization.name} to confirm</Label><Input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} /></div><DialogFooter showCloseButton><Button variant="destructive" disabled={confirmation !== materialization.name || dematerialize.isPending} onClick={() => dematerialize.mutate(materialization, { onSuccess: () => onOpenChange(false) })}>{dematerialize.isPending ? "Turning off…" : "Turn off materialization"}</Button></DialogFooter></DialogContent></Dialog>
}

function BacklogFact({ label, value, detail, failed = false }: { label: string; value: number; detail: string; failed?: boolean }) {
  return <div className="bg-background px-4 py-3"><div className="text-xs text-muted-foreground">{label}</div><div className={`mt-1 font-mono text-xl font-semibold tabular-nums ${failed ? "text-destructive" : ""}`}>{value.toLocaleString()}</div><div className="mt-1 text-[10px] text-muted-foreground">{detail}</div></div>
}

function Fact({ label, value }: { label: string; value: string }) { return <div><dt className="text-muted-foreground">{label}</dt><dd className="mt-0.5 break-words font-medium">{value}</dd></div> }

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`
  return `${(value / 1024 ** 3).toFixed(1)} GB`
}
