import { useState } from "react"
import { ActivityIcon, DatabaseZapIcon, RefreshCwIcon } from "lucide-react"

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
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { useCreateCatalogueMaterialization } from "@/hooks/use-catalogue-materializations"
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
  const inheritedName = source.kind === "view" ? source.view.view_name : ""
  const inheritedDisplayName = source.kind === "view" ? source.view.display_name : ""
  const inheritedDescription = source.kind === "view" ? source.view.description ?? "" : ""
  const inferredScopeColumn = source.kind === "view"
    ? source.view.columns.find((column) => column.toLowerCase() === "document_id") ?? ""
    : "document_id"
  const [name, setName] = useState(inheritedName)
  const [displayName, setDisplayName] = useState(inheritedDisplayName)
  const [description, setDescription] = useState(inheritedDescription)
  const [sourceName, setSourceName] = useState(source.kind === "sql" ? source.query?.name ?? "" : "")
  const [mode, setMode] = useState<"full" | "scope_incremental">(
    source.kind === "view" && inferredScopeColumn ? "scope_incremental" : "full"
  )
  const [scopeColumn, setScopeColumn] = useState(inferredScopeColumn)
  const [partitionColumn, setPartitionColumn] = useState("")
  const [live, setLive] = useState(true)
  const [backfill, setBackfill] = useState(true)
  const [rate, setRate] = useState(60)
  const createMaterialization = useCreateCatalogueMaterialization()
  const createQuery = useCreateSavedQuery()
  const updateQuery = useUpdateSavedQuery()
  const viewSource = source.kind === "view"
  const partitionCandidates = source.kind === "view"
    ? source.view.columns.filter((_, index) => {
        const type = source.view.column_types[index]?.toUpperCase() ?? ""
        return type.includes("DATE") || type.includes("TIMESTAMP")
      })
    : []

  function reset() {
    setName(inheritedName)
    setDisplayName(inheritedDisplayName)
    setDescription(inheritedDescription)
    setSourceName(source.kind === "sql" ? source.query?.name ?? "" : "")
    setMode(source.kind === "view" && inferredScopeColumn ? "scope_incremental" : "full")
    setScopeColumn(inferredScopeColumn)
    setPartitionColumn("")
    setLive(true)
    setBackfill(true)
    setRate(60)
  }

  async function submit() {
    try {
      let queryRevisionId: string | undefined
      let queryId: string | undefined
      if (source.kind === "query") {
        queryId = source.revision.query_id
        queryRevisionId = source.revision.id
      }
      if (source.kind === "sql") {
        if (source.query === null) {
          const saved = await createQuery.mutateAsync({
            name: sourceName,
            description: `Source query for ${displayName || name}`,
            sql: source.sql,
            change_note: "Created while materializing",
          })
          queryId = saved.id
          queryRevisionId = saved.current_revision_id
        } else if (source.query.sql.trim() !== source.sql.trim()) {
          const saved = await updateQuery.mutateAsync({
            query: source.query,
            sql: source.sql,
            name: source.query.name,
            description: source.query.description ?? undefined,
            change_note: "Revision created while materializing",
          })
          queryId = saved.id
          queryRevisionId = saved.current_revision_id
        } else {
          queryId = source.query.id
          queryRevisionId = source.query.current_revision_id
        }
      }
      await createMaterialization.mutateAsync({
        source:
          source.kind === "view"
            ? { kind: "view", view_reference_id: source.view.id! }
            : {
                kind: "query",
                query_id: queryId!,
                active_query_revision_id: queryRevisionId,
              },
        name,
        display_name: displayName || undefined,
        description: description || undefined,
        refresh_mode: mode,
        scope_kind: mode === "scope_incremental" ? "document" : undefined,
        scope_column: mode === "scope_incremental" ? scopeColumn : undefined,
        live_enabled: mode === "scope_incremental" ? live : false,
        backfill_enabled: mode === "scope_incremental" ? backfill : false,
        backfill_scopes_per_minute: rate,
        partition_column: partitionColumn || undefined,
      })
      reset()
      onOpenChange(false)
      const sourceHref = source.kind === "view"
        ? `/catalogue/views/${source.view.id ?? source.view.ducklake_view_uuid}`
        : `/catalogue/queries/${queryId}`
      window.location.href = `${sourceHref}#durable-data`
    } catch {
      // Mutations surface the actionable API error through their shared toast handlers.
    }
  }

  const pending =
    createMaterialization.isPending || createQuery.isPending || updateQuery.isPending
  const sourceLabel =
    source.kind === "query"
      ? source.label
      : source.kind === "view"
        ? source.view.qualified_name
        : source.query?.name ?? "Current workbench SQL"

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => { if (!nextOpen) reset(); onOpenChange(nextOpen) }}>
      <DialogContent className="max-h-[90vh] overflow-y-auto p-0 sm:max-w-2xl">
        <DialogHeader className="border-b bg-gradient-to-r from-primary/10 to-transparent p-5">
          <div className="mb-1 flex size-9 items-center justify-center rounded-xl bg-primary/10 text-primary"><DatabaseZapIcon className="size-4" /></div>
          <DialogTitle className="text-base">Create durable data</DialogTitle>
          <DialogDescription>
            Promote {sourceLabel} into a durable DuckLake table.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-5 px-5">
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
          {!viewSource && <div className="grid gap-3 sm:grid-cols-2">
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
          </div>}
          {!viewSource && <div className="grid gap-1.5">
            <Label>Description</Label>
            <Textarea value={description} onChange={(event) => setDescription(event.target.value)} />
          </div>}
          {viewSource && (
            <div className="rounded-xl border bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
              Atlas will create <code>materialized.{name}</code> and inherit the view’s display name and description.
            </div>
          )}
          <div className="grid gap-2">
            <Label>Maintenance</Label>
            <div className="grid gap-2 sm:grid-cols-2">
              <ModeButton
                icon={RefreshCwIcon}
                selected={mode === "full"}
                onClick={() => setMode("full")}
                title="Full refresh"
                description="Rebuild explicitly from the complete source."
              />
              <ModeButton
                icon={ActivityIcon}
                selected={mode === "scope_incremental"}
                onClick={() => setMode("scope_incremental")}
                title="Live + backfill"
                description="Evaluate one bounded scope at a time."
              />
            </div>
          </div>
          {mode === "scope_incremental" && (
            <div className="grid gap-4 rounded-2xl border bg-muted/20 p-4 shadow-inner">
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="grid gap-1.5">
                  <Label>Scope</Label>
                  <Input value="Document" disabled />
                </div>
                <div className="grid gap-1.5">
                  <Label>Result scope column</Label>
                  {viewSource ? (
                    <Select value={scopeColumn || null} onValueChange={(value) => setScopeColumn(value ?? "")}>
                      <SelectTrigger className="w-full" aria-label="Result scope column">
                        <span>{scopeColumn || "Choose a view output"}</span>
                      </SelectTrigger>
                      <SelectContent>
                        {source.view.columns.map((column) => <SelectItem key={column} value={column}>{column}</SelectItem>)}
                      </SelectContent>
                    </Select>
                  ) : <Input value={scopeColumn} onChange={(event) => setScopeColumn(event.target.value)} />}
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                {viewSource
                  ? <>Atlas filters this output to one <code>document_id</code> at a time and atomically replaces its rows.</>
                  : <>The query must accept <code>$document_id</code> and return {scopeColumn} in every row.</>}
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
            {viewSource ? (
              <Select value={partitionColumn || "__none__"} onValueChange={(value) => setPartitionColumn(value === "__none__" || value === null ? "" : value)}>
                <SelectTrigger className="w-full" aria-label="Daily partition column">
                  <span>{partitionColumn || "No partitioning"}</span>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none__">No partitioning</SelectItem>
                  {partitionCandidates.map((column) => <SelectItem key={column} value={column}>{column}</SelectItem>)}
                </SelectContent>
              </Select>
            ) : <Input value={partitionColumn} onChange={(event) => setPartitionColumn(event.target.value)} placeholder="captured_at" />}
            <p className="text-xs text-muted-foreground">
              A DATE or TIMESTAMP output column; Atlas partitions it by year, month, and day.
            </p>
          </div>
        </div>
        <DialogFooter showCloseButton className="sticky bottom-0 border-t bg-popover/95 p-4 backdrop-blur-xl">
          <Button
            onClick={() => void submit()}
            disabled={
              !name ||
              pending ||
              (source.kind === "sql" && source.query === null && !sourceName.trim()) ||
              (mode === "scope_incremental" && !scopeColumn.trim())
            }
          >
            {pending ? "Creating…" : "Materialize"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ModeButton({
  icon: Icon,
  selected,
  onClick,
  title,
  description,
  disabled = false,
}: {
  icon: typeof RefreshCwIcon
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
      className={`rounded-2xl border p-4 text-left transition-all disabled:cursor-not-allowed disabled:opacity-50 ${selected ? "border-primary/40 bg-primary/10 shadow-sm" : "hover:-translate-y-0.5 hover:bg-muted/40 hover:shadow-sm"}`}
    >
      <span className={`mb-3 flex size-8 items-center justify-center rounded-xl ${selected ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground"}`}><Icon className="size-3.5" /></span>
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
