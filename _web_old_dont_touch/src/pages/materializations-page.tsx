import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { LoaderCircleIcon, RefreshCwIcon } from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Progress,
  ProgressLabel,
  ProgressValue,
} from "@/components/ui/progress"
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
import type { MaterializationRun } from "@/types/operations"

export function MaterializationsPage() {
  const client = useQueryClient()
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
          body: "{}",
        }
      )
      if (!response.ok) throw await apiErrorFromResponse(response)
      return (await response.json()) as MaterializationRun
    },
    onSuccess: () => {
      toast.success("Complete materialization rebuild queued.")
      void client.invalidateQueries({ queryKey: ["materialization-runs"] })
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
  const active = (runs.data ?? []).some((run) =>
    ["queued", "planning", "running", "activating"].includes(run.status)
  )

  return (
    <div className="flex min-h-0 w-full flex-col gap-4">
      <section className="overflow-hidden rounded-lg border bg-card/70 shadow-xs">
        <div className="flex flex-wrap items-center justify-between gap-4 border-b bg-muted/20 px-4 py-4">
          <div>
            <h1 className="text-sm font-medium">Materialized catalogue</h1>
            <p className="mt-1 max-w-2xl text-xs text-muted-foreground">
              Rebuilds every discovered projection, catches up new visits, and
              activates the complete registry atomically.
            </p>
          </div>
          <Button
            size="sm"
            disabled={create.isPending || active}
            onClick={() => create.mutate()}
          >
            {create.isPending ? (
              <LoaderCircleIcon className="animate-spin" />
            ) : (
              <RefreshCwIcon />
            )}
            Rebuild
          </Button>
        </div>

        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead>Rebuild</TableHead>
              <TableHead className="w-36">Status</TableHead>
              <TableHead className="w-[22rem]">Progress</TableHead>
              <TableHead className="w-36 text-right">Output</TableHead>
              <TableHead className="w-36 text-right">Started</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(runs.data ?? []).map((run) => (
              <TableRow key={run.id}>
                <TableCell>
                  <code className="font-mono text-xs">{run.id.slice(0, 12)}</code>
                  <p className="mt-1 text-[0.6875rem] text-muted-foreground">
                    source snapshot {run.source_snapshot}
                    {run.activation_snapshot !== null &&
                      ` · activated ${run.activation_snapshot}`}
                  </p>
                  {run.error && (
                    <p className="mt-1 max-w-xl text-xs text-destructive">
                      {run.error}
                    </p>
                  )}
                </TableCell>
                <TableCell>
                  <RunStatusBadge run={run} />
                </TableCell>
                <TableCell>
                  <Progress value={Math.round(run.progress * 100)}>
                    <ProgressLabel>
                      {run.completed_batches.toLocaleString()} of{" "}
                      {run.total_batches.toLocaleString()} batches
                    </ProgressLabel>
                    <ProgressValue>
                      {() => `${Math.round(run.progress * 100)}%`}
                    </ProgressValue>
                  </Progress>
                </TableCell>
                <TableCell className="text-right text-xs tabular-nums">
                  {run.output_rows.toLocaleString()} rows
                  <p className="text-[0.6875rem] text-muted-foreground">
                    {formatBytes(run.output_bytes)}
                  </p>
                </TableCell>
                <TableCell className="text-right text-xs text-muted-foreground">
                  {formatRelativeDate(run.started_at ?? run.created_at)}
                </TableCell>
              </TableRow>
            ))}
            {!runs.isLoading && (runs.data?.length ?? 0) === 0 && (
              <TableRow>
                <TableCell
                  colSpan={5}
                  className="h-28 text-center text-xs text-muted-foreground"
                >
                  No rebuilds yet.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </section>
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
        ["planning", "running", "activating"].includes(run.status) &&
          "border-primary/30 bg-primary/10 text-primary"
      )}
    >
      {run.status}
    </Badge>
  )
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`
  const units = ["KiB", "MiB", "GiB", "TiB"]
  let scaled = value
  let unit = -1
  do {
    scaled /= 1024
    unit += 1
  } while (scaled >= 1024 && unit < units.length - 1)
  return `${scaled.toFixed(scaled >= 10 ? 0 : 1)} ${units[unit]}`
}

function formatRelativeDate(value: string) {
  const elapsed = Date.now() - new Date(value).getTime()
  const minutes = Math.max(0, Math.round(elapsed / 60_000))
  if (minutes < 1) return "just now"
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.round(hours / 24)}d ago`
}
