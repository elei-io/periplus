import { useEffect, useState } from "react"

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
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { useCreateMaterializedView } from "@/hooks/use-materialized-views"
import { useCreateSavedQuery, useUpdateSavedQuery } from "@/hooks/use-saved-queries"
import type {
  CatalogueViewRecord,
  SavedQueryDetail,
  SavedQueryRevision,
} from "@/types/catalogue"

type MaterializationSource =
  | { kind: "query"; revision: SavedQueryRevision; label: string }
  | { kind: "view"; view: CatalogueViewRecord }
  | { kind: "sql"; sql: string; query: SavedQueryDetail | null }

export function MaterializeQueryDialog({
  open,
  onOpenChange,
  source,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  source: MaterializationSource
}) {
  const [name, setName] = useState("")
  const [displayName, setDisplayName] = useState("")
  const [description, setDescription] = useState("")
  const [sourceName, setSourceName] = useState("")
  const [mode, setMode] = useState<"full" | "scope_incremental">("full")
  const [scopeColumn, setScopeColumn] = useState("document_id")
  const [partitionColumn, setPartitionColumn] = useState("")
  const [live, setLive] = useState(true)
  const [backfill, setBackfill] = useState(true)
  const [rate, setRate] = useState(60)
  const createMaterialized = useCreateMaterializedView()
  const createQuery = useCreateSavedQuery()
  const updateQuery = useUpdateSavedQuery()
  const viewSource = source.kind === "view"

  useEffect(() => {
    if (!open) return
    setName("")
    setDisplayName("")
    setDescription("")
    setSourceName(source.kind === "sql" ? source.query?.name ?? "" : "")
    setMode("full")
    setScopeColumn("document_id")
    setPartitionColumn("")
    setLive(true)
    setBackfill(true)
    setRate(60)
  }, [open, source])

  async function submit() {
    try {
      let queryRevisionId: string | undefined
      if (source.kind === "query") queryRevisionId = source.revision.id
      if (source.kind === "sql") {
        if (source.query === null) {
          const saved = await createQuery.mutateAsync({
            name: sourceName,
            description: `Source query for ${displayName || name}`,
            sql: source.sql,
            change_note: "Created while materializing",
          })
          queryRevisionId = saved.current_revision_id
        } else if (source.query.sql.trim() !== source.sql.trim()) {
          const saved = await updateQuery.mutateAsync({
            query: source.query,
            sql: source.sql,
            name: source.query.name,
            description: source.query.description ?? undefined,
            change_note: "Revision created while materializing",
          })
          queryRevisionId = saved.current_revision_id
        } else {
          queryRevisionId = source.query.current_revision_id
        }
      }
      await createMaterialized.mutateAsync({
        name,
        display_name: displayName || undefined,
        description: description || undefined,
        query_revision_id: queryRevisionId,
        source_view_uuid: source.kind === "view" ? source.view.ducklake_view_uuid : undefined,
        refresh_mode: mode,
        scope_kind: mode === "scope_incremental" ? "document" : undefined,
        scope_column: mode === "scope_incremental" ? scopeColumn : undefined,
        live_enabled: mode === "scope_incremental" ? live : false,
        backfill_enabled: mode === "scope_incremental" ? backfill : false,
        backfill_scopes_per_minute: rate,
        partition_column: partitionColumn || undefined,
      })
      onOpenChange(false)
      window.location.href = "/catalogue/materialized-views"
    } catch {
      // Mutations surface the actionable API error through their shared toast handlers.
    }
  }

  const pending =
    createMaterialized.isPending || createQuery.isPending || updateQuery.isPending
  const sourceLabel =
    source.kind === "query"
      ? source.label
      : source.kind === "view"
        ? source.view.qualified_name
        : source.query?.name ?? "Current workbench SQL"

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Create materialized view</DialogTitle>
          <DialogDescription>
            Promote {sourceLabel} into a durable DuckLake table.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-5">
          {source.kind === "sql" && source.query === null && (
            <div className="grid gap-1.5">
              <Label>Saved source query name</Label>
              <Input
                value={sourceName}
                onChange={(event) => setSourceName(event.target.value)}
                placeholder="Document links source"
              />
              <p className="text-xs text-muted-foreground">
                Atlas saves the current SQL first so the materialization has immutable provenance.
              </p>
            </div>
          )}
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="grid gap-1.5">
              <Label>Table name</Label>
              <Input
                value={name}
                onChange={(event) =>
                  setName(event.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_"))
                }
                placeholder="document_links"
              />
            </div>
            <div className="grid gap-1.5">
              <Label>Display name</Label>
              <Input
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                placeholder="Document links"
              />
            </div>
          </div>
          <div className="grid gap-1.5">
            <Label>Description</Label>
            <Textarea value={description} onChange={(event) => setDescription(event.target.value)} />
          </div>
          <div className="grid gap-2">
            <Label>Maintenance</Label>
            <div className="grid gap-2 sm:grid-cols-2">
              <ModeButton
                selected={mode === "full"}
                onClick={() => setMode("full")}
                title="Full refresh"
                description="Rebuild explicitly from the complete source."
              />
              <ModeButton
                selected={mode === "scope_incremental"}
                onClick={() => setMode("scope_incremental")}
                title="Live + backfill"
                description="Evaluate one bounded scope at a time."
                disabled={viewSource}
              />
            </div>
            {viewSource && (
              <p className="text-xs text-muted-foreground">
                Views can be fully materialized. Incremental maintenance requires a parameterized saved query.
              </p>
            )}
          </div>
          {mode === "scope_incremental" && (
            <div className="grid gap-4 rounded-lg border bg-muted/20 p-4">
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="grid gap-1.5">
                  <Label>Scope</Label>
                  <Input value="Document" disabled />
                </div>
                <div className="grid gap-1.5">
                  <Label>Result scope column</Label>
                  <Input value={scopeColumn} onChange={(event) => setScopeColumn(event.target.value)} />
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                The query must accept <code>$document_id</code> and return {scopeColumn} in every row.
              </p>
              <ToggleRow
                label="Live maintenance"
                description="Process new source changes from the durable CDC cursor."
                checked={live}
                onCheckedChange={setLive}
              />
              <ToggleRow
                label="Historical backfill"
                description="Populate scopes visible at the activation snapshot."
                checked={backfill}
                onCheckedChange={setBackfill}
              />
              <div className="grid gap-1.5 sm:max-w-xs">
                <Label>Backfill scopes per minute</Label>
                <Input
                  type="number"
                  min={1}
                  max={10_000}
                  value={rate}
                  onChange={(event) => setRate(Number(event.target.value) || 1)}
                />
              </div>
            </div>
          )}
          <div className="grid gap-1.5">
            <Label>Daily partition column (optional)</Label>
            <Input
              value={partitionColumn}
              onChange={(event) => setPartitionColumn(event.target.value)}
              placeholder="captured_at"
            />
            <p className="text-xs text-muted-foreground">
              A DATE or TIMESTAMP output column; Atlas partitions it by year, month, and day.
            </p>
          </div>
        </div>
        <DialogFooter showCloseButton>
          <Button
            onClick={() => void submit()}
            disabled={
              !name ||
              pending ||
              (source.kind === "sql" && source.query === null && !sourceName.trim()) ||
              (mode === "scope_incremental" && !scopeColumn.trim())
            }
          >
            {pending ? "Creating…" : "Create materialized view"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ModeButton({
  selected,
  onClick,
  title,
  description,
  disabled = false,
}: {
  selected: boolean
  onClick: () => void
  title: string
  description: string
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={`rounded-lg border p-3 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${selected ? "border-primary bg-primary/10" : "hover:bg-muted"}`}
    >
      <span className="block text-sm font-medium">{title}</span>
      <span className="mt-1 block text-xs text-muted-foreground">{description}</span>
    </button>
  )
}

function ToggleRow({
  label,
  description,
  checked,
  onCheckedChange,
}: {
  label: string
  description: string
  checked: boolean
  onCheckedChange: (value: boolean) => void
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div>
        <div className="text-sm font-medium">{label}</div>
        <div className="text-xs text-muted-foreground">{description}</div>
      </div>
      <Switch checked={checked} onCheckedChange={onCheckedChange} />
    </div>
  )
}
