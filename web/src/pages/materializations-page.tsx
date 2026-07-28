import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  ChevronDownIcon,
  Clock3Icon,
  DatabaseZapIcon,
  LoaderCircleIcon,
  RefreshCwIcon,
} from "lucide-react"
import { Fragment, useState } from "react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
import { cn } from "@/lib/utils"
import type {
  MaterializationProjection,
  MaterializationRun,
} from "@/types/operations"

type ProjectionDefinition = {
  value: MaterializationProjection
  label: string
  description: string
  source: string
}

const projections: ProjectionDefinition[] = [
  {
    value: "html_elements",
    label: "HTML elements",
    description: "Versioned structural DOM elements for each HTML body.",
    source: "ingest.documents",
  },
  {
    value: "jsonld_values",
    label: "JSON-LD values",
    description: "Complete parsed JSON-LD payloads embedded in documents.",
    source: "ingest.documents",
  },
  {
    value: "links",
    label: "Links",
    description: "Stable normalized source-target page pairs.",
    source: "ingest.documents",
  },
  {
    value: "link_observations",
    label: "Link observations",
    description: "Document-owned evidence for each observed anchor element.",
    source: "ingest.documents",
  },
  {
    value: "pages",
    label: "Pages",
    description: "One durable page identity for every normalized URL.",
    source: "ingest.visits",
  },
  {
    value: "page_observations",
    label: "Page observations",
    description: "Visit-owned page, document, and observation-time evidence.",
    source: "ingest.visits",
  },
]

const projectionValues = projections.map((projection) => projection.value)
export function MaterializationsPage() {
  const client = useQueryClient()
  const [mode, setMode] = useState<"backfill" | "rebuild">("rebuild")
  const [selected, setSelected] = useState<MaterializationProjection[]>([
    "pages",
  ])
  const [expanded, setExpanded] = useState<MaterializationProjection[]>([])
  const runs = useQuery({
    queryKey: ["materialization-runs"],
    queryFn: async () => {
      const response = await fetch(apiUrl("/operations/materializations/runs"))
      if (!response.ok) throw await apiErrorFromResponse(response)
      return (await response.json()) as MaterializationRun[]
    },
    refetchInterval: 2_000,
  })
  const create = useMutation({
    mutationFn: async () => {
      const response = await fetch(
        apiUrl("/operations/materializations/runs"),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode, stages: selected }),
        }
      )
      if (!response.ok) throw await apiErrorFromResponse(response)
      return (await response.json()) as MaterializationRun
    },
    onSuccess: (run) => {
      toast.success(`Queued ${run.mode} run.`)
      void client.invalidateQueries({ queryKey: ["materialization-runs"] })
    },
    onError: (error) => toast.error(extractApiError(error)),
  })

  const included = new Set(selected)
  const allSelected = included.size === projections.length

  const toggle = (projection: MaterializationProjection) => {
    setSelected((current) =>
      current.includes(projection)
        ? current.filter((item) => item !== projection)
        : [...current, projection]
    )
  }

  const toggleExpanded = (projection: MaterializationProjection) => {
    setExpanded((current) =>
      current.includes(projection)
        ? current.filter((item) => item !== projection)
        : [...current, projection]
    )
  }

  return (
    <div className="flex min-h-0 w-full flex-col gap-4">
      <section className="flex flex-wrap items-end justify-between gap-4 border-b pb-4">
        <div>
          <div className="flex items-center gap-2">
            <DatabaseZapIcon className="size-4 text-muted-foreground" />
            <h1 className="text-lg font-medium">Material tables</h1>
            <Badge variant="outline">{projections.length}</Badge>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            Select tables to repair in place or rebuild through a shadow
            generation.
          </p>
        </div>
        <Badge
          variant={included.size > 0 ? "secondary" : "outline"}
          className="h-6 px-2.5"
        >
          {included.size} selected
        </Badge>
      </section>

      <section className="overflow-hidden rounded-lg border bg-card/70 shadow-xs">
        <div className="flex flex-wrap items-center justify-between gap-4 border-b bg-muted/20 px-4 py-3">
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex rounded-md border bg-background/70 p-0.5">
              <Button
                size="sm"
                variant={mode === "rebuild" ? "secondary" : "ghost"}
                className="h-7 rounded-sm px-3"
                onClick={() => setMode("rebuild")}
              >
                Shadow rebuild
              </Button>
              <Button
                size="sm"
                variant={mode === "backfill" ? "secondary" : "ghost"}
                className="h-7 rounded-sm px-3"
                onClick={() => setMode("backfill")}
              >
                Live backfill
              </Button>
            </div>
            <p className="max-w-xl text-xs text-muted-foreground">
              {mode === "rebuild"
                ? "Builds bounded shadow generations, catches up source changes, then swaps atomically."
                : "Fills missing live identity slices in bounded, resumable batches."}
            </p>
          </div>
          <Button
            size="sm"
            disabled={create.isPending || selected.length === 0}
            onClick={() => create.mutate()}
          >
            {create.isPending ? (
              <LoaderCircleIcon className="animate-spin" />
            ) : (
              <RefreshCwIcon />
            )}
            Start {mode}
            {included.size > 0 && ` · ${included.size}`}
          </Button>
        </div>

        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="w-11 pl-4">
                <Checkbox
                  aria-label={
                    allSelected ? "Deselect all tables" : "Select all tables"
                  }
                  checked={allSelected}
                  indeterminate={included.size > 0 && !allSelected}
                  onCheckedChange={(checked) =>
                    setSelected(checked ? projectionValues : [])
                  }
                />
              </TableHead>
              <TableHead>Material table</TableHead>
              <TableHead className="w-44">Source</TableHead>
              <TableHead className="w-52">Latest maintenance</TableHead>
              <TableHead className="w-12">
                <span className="sr-only">History</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {projections.map((projection) => {
              const history = (runs.data ?? []).filter((run) =>
                run.stages.includes(projection.value)
              )
              const latest = history[0]
              const isExpanded = expanded.includes(projection.value)
              const isIncluded = included.has(projection.value)
              return (
                <Fragment key={projection.value}>
                  <TableRow
                    data-state={isIncluded ? "selected" : undefined}
                    className="group cursor-pointer"
                    onClick={() => toggleExpanded(projection.value)}
                  >
                    <TableCell
                      className="pl-4"
                      onClick={(event) => event.stopPropagation()}
                    >
                      <Checkbox
                        aria-label={`Include ${projection.label}`}
                        checked={isIncluded}
                        onCheckedChange={() => toggle(projection.value)}
                      />
                    </TableCell>
                    <TableCell className="py-3">
                      <div className="flex items-center gap-2">
                        <code className="font-mono text-xs font-medium text-foreground">
                          material.{projection.value}
                        </code>
                      </div>
                      <p className="mt-1 max-w-xl whitespace-normal text-xs text-muted-foreground">
                        {projection.description}
                      </p>
                    </TableCell>
                    <TableCell>
                      <code className="font-mono text-[0.6875rem] text-muted-foreground">
                        {projection.source}
                      </code>
                    </TableCell>
                    <TableCell>
                      {latest ? (
                        <div className="flex items-center gap-2">
                          <RunStatusBadge run={latest} />
                          <span className="text-[0.6875rem] text-muted-foreground">
                            {formatRelativeDate(latest.created_at)}
                          </span>
                        </div>
                      ) : (
                        <span className="text-xs text-muted-foreground">
                          No runs yet
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="pr-4 text-right">
                      <Button
                        size="icon-sm"
                        variant="ghost"
                        aria-label={`Show ${projection.label} history`}
                        aria-expanded={isExpanded}
                        onClick={(event) => {
                          event.stopPropagation()
                          toggleExpanded(projection.value)
                        }}
                      >
                        <ChevronDownIcon
                          className={cn(
                            "transition-transform",
                            isExpanded && "rotate-180"
                          )}
                        />
                      </Button>
                    </TableCell>
                  </TableRow>
                  {isExpanded && (
                    <TableRow className="hover:bg-transparent">
                      <TableCell
                        colSpan={5}
                        className="bg-muted/15 px-4 py-0"
                      >
                        <RunHistory
                          projection={projection}
                          runs={history.slice(0, 5)}
                        />
                      </TableCell>
                    </TableRow>
                  )}
                </Fragment>
              )
            })}
          </TableBody>
        </Table>
      </section>

      <p className="px-1 text-[0.6875rem] text-muted-foreground">
        Tables are independently selectable. Selected tables with the same
        source share one bounded scan and projection pass.
      </p>
    </div>
  )
}

function RunHistory({
  projection,
  runs,
}: {
  projection: ProjectionDefinition
  runs: MaterializationRun[]
}) {
  return (
    <div className="py-4 pl-9 pr-10">
      <div className="mb-3 flex items-center gap-2">
        <Clock3Icon className="size-3.5 text-muted-foreground" />
        <h2 className="text-xs font-medium">
          Recent maintenance for {projection.label}
        </h2>
      </div>
      {runs.length === 0 ? (
        <div className="rounded-md border border-dashed px-4 py-5 text-center text-xs text-muted-foreground">
          This table has no rebuild or backfill history yet.
        </div>
      ) : (
        <div className="overflow-hidden rounded-md border bg-background/50">
          {runs.map((run, index) => (
            <div
              key={run.id}
              className={cn(
                "grid grid-cols-[6rem_5rem_7rem_minmax(10rem,1fr)_7rem] items-center gap-3 px-3 py-2 text-xs",
                index > 0 && "border-t"
              )}
            >
              <code className="font-mono text-[0.6875rem] text-muted-foreground">
                {run.id.slice(0, 8)}
              </code>
              <span className="capitalize">{run.mode}</span>
              <RunStatusBadge run={run} />
              <span className="text-muted-foreground">
                {run.current_stage}/{run.stages.length} stages ·{" "}
                {run.source_items.toLocaleString()} inputs
              </span>
              <span className="text-right text-muted-foreground">
                {run.output_rows.toLocaleString()} rows
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function RunStatusBadge({ run }: { run: MaterializationRun }) {
  return (
    <Badge
      variant={
        run.status === "failed"
          ? "destructive"
          : run.status === "completed"
            ? "secondary"
            : "outline"
      }
      title={run.error ?? undefined}
      className={cn(
        "capitalize",
        run.status === "running" &&
          "border-primary/30 bg-primary/10 text-primary"
      )}
    >
      {run.status}
    </Badge>
  )
}

function formatRelativeDate(value: string) {
  const elapsed = Date.now() - new Date(value).getTime()
  const minutes = Math.max(0, Math.round(elapsed / 60_000))
  if (minutes < 1) return "just now"
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.round(hours / 24)
  return `${days}d ago`
}
