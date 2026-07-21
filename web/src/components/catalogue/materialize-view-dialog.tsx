import { useState } from "react"
import { DatabaseZapIcon } from "lucide-react"

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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select"
import { useCreateCatalogueMaterialization } from "@/hooks/use-catalogue-materializations"
import type { CatalogueViewRecord } from "@/types/catalogue"

const sourceTables = [
  "urls",
  "documents",
  "crawls",
  "elements",
  "artifacts",
  "crawl_attempts",
  "crawl_steps",
] as const

export function MaterializeViewDialog({
  open,
  onOpenChange,
  view,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  view: CatalogueViewRecord
}) {
  const inferred =
    sourceTables.find((table) => view.sql.toLowerCase().includes(table)) ??
    "documents"
  const [sourceTable, setSourceTable] = useState<string>(inferred)
  const [delay, setDelay] = useState(1)
  const [partitionColumn, setPartitionColumn] = useState("")
  const create = useCreateCatalogueMaterialization()
  const partitionCandidates = view.columns.filter((_, index) => {
    const type = view.column_types[index]?.toUpperCase() ?? ""
    return type.includes("DATE") || type.includes("TIMESTAMP")
  })

  async function submit() {
    try {
      await create.mutateAsync({
        view_reference_id: view.id!,
        name: view.view_name,
        display_name: view.slug,
        description: view.description ?? undefined,
        source_table: sourceTable,
        refresh_delay_seconds: delay,
        partition_column: partitionColumn || undefined,
      })
      onOpenChange(false)
    } catch {
      // The shared mutation surfaces the API error.
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <div className="mb-1 flex size-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <DatabaseZapIcon className="size-4" />
          </div>
          <DialogTitle>Materialize {view.slug}</DialogTitle>
          <DialogDescription>
            Atlas will snapshot the whole view, then refresh it when the driving
            DuckLake table publishes a CDC tick.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-5">
          <div className="grid gap-1.5">
            <Label>Driving table</Label>
            <Select value={sourceTable} onValueChange={(value) => setSourceTable(value ?? inferred)}>
              <SelectTrigger className="w-full"><span>{sourceTable}</span></SelectTrigger>
              <SelectContent>
                {sourceTables.map((table) => (
                  <SelectItem key={table} value={table}>{table}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              A committed DML change to this table schedules a refresh.
            </p>
          </div>
          <div className="grid gap-1.5">
            <Label>Coalescing delay (seconds)</Label>
            <Input
              type="number"
              min={0}
              max={3600}
              step={0.1}
              value={delay}
              onChange={(event) => setDelay(Number(event.target.value) || 0)}
            />
          </div>
          <div className="grid gap-1.5">
            <Label>Daily partition column</Label>
            <Select
              value={partitionColumn || "__none__"}
              onValueChange={(value) =>
                setPartitionColumn(value === "__none__" || value === null ? "" : value)
              }
            >
              <SelectTrigger className="w-full"><span>{partitionColumn || "No partitioning"}</span></SelectTrigger>
              <SelectContent>
                <SelectItem value="__none__">No partitioning</SelectItem>
                {partitionCandidates.map((column) => (
                  <SelectItem key={column} value={column}>{column}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter showCloseButton>
          <Button onClick={() => void submit()} disabled={create.isPending}>
            {create.isPending ? "Materializing…" : "Materialize"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
