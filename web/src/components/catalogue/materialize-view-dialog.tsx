import { useState } from "react"
import { DatabaseZapIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/select"
import { useCreateCatalogueMaterialization } from "@/hooks/use-catalogue-materializations"
import type { CatalogueViewRecord } from "@/types/catalogue"

export function MaterializeViewDialog({ open, onOpenChange, view }: { open: boolean; onOpenChange: (open: boolean) => void; view: CatalogueViewRecord }) {
  const inferredKind = view.columns.some((column) => column.toLowerCase() === "document_id") ? "document" : "crawl"
  const inferredColumn = view.columns.find((column) => column.toLowerCase() === `${inferredKind}_id`) ?? ""
  const [scopeKind, setScopeKind] = useState<"document" | "crawl">(inferredKind)
  const [scopeColumn, setScopeColumn] = useState(inferredColumn)
  const [rate, setRate] = useState(60)
  const [partitionColumn, setPartitionColumn] = useState("")
  const create = useCreateCatalogueMaterialization()
  const partitionCandidates = view.columns.filter((_, index) => {
    const type = view.column_types[index]?.toUpperCase() ?? ""
    return type.includes("DATE") || type.includes("TIMESTAMP")
  })

  function reset() {
    setScopeKind(inferredKind)
    setScopeColumn(inferredColumn)
    setRate(60)
    setPartitionColumn("")
  }

  function chooseKind(value: "document" | "crawl") {
    setScopeKind(value)
    setScopeColumn(
      view.columns.find((column) => column.toLowerCase() === `${value}_id`) ?? ""
    )
  }

  async function submit() {
    try {
      await create.mutateAsync({
        view_reference_id: view.id!,
        name: view.view_name,
        display_name: view.display_name,
        description: view.description ?? undefined,
        scope_kind: scopeKind,
        scope_column: scopeColumn,
        backfill_scopes_per_minute: rate,
        partition_column: partitionColumn || undefined,
      })
      reset()
      onOpenChange(false)
    } catch {
      // The shared mutation surfaces the API error.
    }
  }

  return <Dialog open={open} onOpenChange={(nextOpen) => { if (!nextOpen) reset(); onOpenChange(nextOpen) }}>
    <DialogContent className="max-h-[90vh] overflow-y-auto p-0 sm:max-w-xl">
      <DialogHeader className="border-b bg-gradient-to-r from-primary/10 to-transparent p-5">
        <div className="mb-1 flex size-9 items-center justify-center rounded-xl bg-primary/10 text-primary"><DatabaseZapIcon className="size-4" /></div>
        <DialogTitle className="text-base">Materialize {view.display_name}</DialogTitle>
        <DialogDescription>Atlas will backfill existing results and keep this view updated as matching crawl data changes.</DialogDescription>
      </DialogHeader>
      <div className="grid gap-5 px-5">
        <div className="grid gap-1.5">
          <Label>Incremental unit</Label>
          <Select value={scopeKind} onValueChange={(value) => chooseKind(value as "document" | "crawl")}>
            <SelectTrigger className="w-full" aria-label="Incremental unit"><span>{scopeKind === "document" ? "Document" : "Crawl"}</span></SelectTrigger>
            <SelectContent><SelectItem value="document">Document</SelectItem><SelectItem value="crawl">Crawl</SelectItem></SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">When one {scopeKind} changes, Atlas replaces only the rows belonging to it.</p>
        </div>
        <div className="grid gap-1.5">
          <Label>Discriminator column</Label>
          <Select value={scopeColumn || null} onValueChange={(value) => setScopeColumn(value ?? "")}>
            <SelectTrigger className="w-full" aria-label="Discriminator column"><span>{scopeColumn || "Choose an output column"}</span></SelectTrigger>
            <SelectContent>{view.columns.map((column, index) => <SelectItem key={column} value={column}>{column} · {view.column_types[index] ?? "unknown"}</SelectItem>)}</SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">This output identifies which rows belong to the changed {scopeKind}. It is usually <code>{scopeKind}_id</code>.</p>
        </div>
        <details className="group rounded-xl border">
          <summary className="cursor-pointer list-none px-4 py-3 text-sm font-medium marker:hidden">Advanced settings <span className="ml-1 text-xs font-normal text-muted-foreground group-open:hidden">· optional</span></summary>
          <div className="grid gap-4 border-t p-4">
            <div className="grid gap-1.5"><Label>Backfill units per minute</Label><Input type="number" min={1} max={10_000} value={rate} onChange={(event) => setRate(Number(event.target.value) || 1)} /></div>
            <div className="grid gap-1.5"><Label>Daily partition column</Label><Select value={partitionColumn || "__none__"} onValueChange={(value) => setPartitionColumn(value === "__none__" || value === null ? "" : value)}><SelectTrigger className="w-full" aria-label="Daily partition column"><span>{partitionColumn || "No partitioning"}</span></SelectTrigger><SelectContent><SelectItem value="__none__">No partitioning</SelectItem>{partitionCandidates.map((column) => <SelectItem key={column} value={column}>{column}</SelectItem>)}</SelectContent></Select></div>
          </div>
        </details>
      </div>
      <DialogFooter showCloseButton className="sticky bottom-0 border-t bg-popover/95 p-4 backdrop-blur-xl"><Button onClick={() => void submit()} disabled={!scopeColumn || create.isPending}>{create.isPending ? "Materializing…" : "Materialize"}</Button></DialogFooter>
    </DialogContent>
  </Dialog>
}
