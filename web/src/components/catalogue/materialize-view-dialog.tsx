import { useState } from "react"
import {
  DatabaseZapIcon,
  LoaderCircleIcon,
  TriangleAlertIcon,
} from "lucide-react"

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
  SelectValue,
} from "@/components/ui/select"
import {
  useCreateCatalogueMaterialization,
  useMaterializationEligibility,
} from "@/hooks/use-catalogue-materializations"
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
  const [refreshStrategy, setRefreshStrategy] = useState<
    "keyed" | "append" | "full"
  >("keyed")
  const [keyColumns, setKeyColumns] = useState<string[]>([])
  const [delay, setDelay] = useState(1)
  const [partitionColumn, setPartitionColumn] = useState("")
  const create = useCreateCatalogueMaterialization()
  const keys = keyColumns
  const eligibility = useMaterializationEligibility({
    viewReferenceId: view.id,
    sourceTable,
    refreshStrategy,
    keyColumns: refreshStrategy === "full" ? [] : keys,
    enabled: open,
  })
  const partitionCandidates = view.columns.filter((_, index) => {
    const type = view.column_types[index]?.toUpperCase() ?? ""
    return type.includes("DATE") || type.includes("TIMESTAMP")
  })

  async function submit() {
    if (!eligibility.data?.eligible) return
    try {
      await create.mutateAsync({
        view_reference_id: view.id!,
        name: view.view_name,
        display_name: view.slug,
        description: view.description ?? undefined,
        source_table: sourceTable,
        refresh_strategy: refreshStrategy,
        key_columns: refreshStrategy === "full" ? [] : keys,
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
            Atlas will snapshot the view, then apply committed changes from its
            driving DuckLake table.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-5">
          <div className="grid gap-1.5">
            <Label>Driving table</Label>
            <Select
              value={sourceTable}
              onValueChange={(value) => setSourceTable(value ?? inferred)}
            >
              <SelectTrigger className="w-full">
                <span>{sourceTable}</span>
              </SelectTrigger>
              <SelectContent>
                {sourceTables.map((table) => (
                  <SelectItem key={table} value={table}>
                    {table}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              A committed DML change to this table schedules a refresh.
            </p>
          </div>
          <div className="grid gap-1.5">
            <Label>Refresh strategy</Label>
            <Select
              value={refreshStrategy}
              onValueChange={(value) =>
                setRefreshStrategy(
                  (value ?? "keyed") as "keyed" | "append" | "full"
                )
              }
            >
              <SelectTrigger className="w-full">
                <span>
                  {refreshStrategy === "keyed"
                    ? "Replace affected groups"
                    : refreshStrategy === "append"
                      ? "Append new rows"
                      : "Rebuild everything"}
                </span>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="keyed">Replace affected groups</SelectItem>
                <SelectItem value="append">Append new rows</SelectItem>
                <SelectItem value="full">Rebuild everything</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              {refreshStrategy === "keyed"
                ? "Recomputes only groups whose composite refresh key changed."
                : refreshStrategy === "append"
                  ? "Accepts inserts only; the key must uniquely identify each result row."
                  : "Recomputes the complete result after coalescing changes."}
            </p>
          </div>
          {refreshStrategy !== "full" ? (
            <div className="grid gap-1.5">
              <Label>
                {refreshStrategy === "keyed"
                  ? "Refresh key columns"
                  : "Row identity columns"}
              </Label>
              <Select
                multiple
                value={keyColumns}
                onValueChange={(value) => setKeyColumns(value)}
              >
                <SelectTrigger className="h-auto min-h-7 w-full">
                  <SelectValue placeholder="Select columns">
                    {(value: string[]) =>
                      value.length === 0
                        ? "Select columns"
                        : value
                            .map((column, index) => `${index + 1}. ${column}`)
                            .join(", ")
                    }
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {view.columns.map((column, index) => (
                    <SelectItem key={column} value={column}>
                      <span className="font-mono">{column}</span>
                      <span className="ml-auto text-muted-foreground">
                        {view.column_types[index] ?? "UNKNOWN"}
                      </span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                Select columns in key order. Each must also be present in the
                driving table.
              </p>
            </div>
          ) : null}
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
                setPartitionColumn(
                  value === "__none__" || value === null ? "" : value
                )
              }
            >
              <SelectTrigger className="w-full">
                <span>{partitionColumn || "No partitioning"}</span>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__none__">No partitioning</SelectItem>
                {partitionCandidates.map((column) => (
                  <SelectItem key={column} value={column}>
                    {column}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div
            role="status"
            className={`rounded-md border px-3 py-2 text-xs ${
              eligibility.data?.eligible
                ? "border-emerald-500/30 bg-emerald-500/5 text-emerald-700 dark:text-emerald-300"
                : eligibility.data
                  ? "border-destructive/30 bg-destructive/5 text-destructive"
                  : "text-muted-foreground"
            }`}
          >
            {refreshStrategy !== "full" && keys.length === 0 ? (
              "Select a stable result key to check eligibility."
            ) : eligibility.isPending || eligibility.isDebouncing ? (
              <span className="flex items-center gap-2">
                <LoaderCircleIcon className="size-3.5 animate-spin" />
                Checking materialization eligibility…
              </span>
            ) : eligibility.data?.eligible ? (
              "This view can be materialized with these settings."
            ) : eligibility.data ? (
              <span className="flex items-start gap-2">
                <TriangleAlertIcon className="mt-0.5 size-3.5 shrink-0" />
                {eligibility.data.diagnostics[0]?.message ??
                  "This view cannot be materialized with these settings."}
              </span>
            ) : (
              "Eligibility could not be checked."
            )}
          </div>
        </div>
        <DialogFooter showCloseButton>
          <Button
            onClick={() => void submit()}
            disabled={create.isPending || !eligibility.data?.eligible}
          >
            {create.isPending ? "Materializing…" : "Materialize"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
