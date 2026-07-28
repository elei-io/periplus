import {
  GitForkIcon,
  LoaderCircleIcon,
  PauseIcon,
  PlayIcon,
  PlusIcon,
  Settings2Icon,
  Trash2Icon,
} from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"
import { toast } from "sonner"

import { GraphCanvas } from "@/components/crawl-graph/graph-canvas"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  useCreateCrawlGraph,
  useCreateCrawlGraphNode,
  useCrawlGraph,
  useCrawlGraphs,
  useDeleteCrawlGraph,
  useDeleteCrawlGraphNode,
  useActiveGraphRuns,
  useCancelGraphRun,
  useGraphRun,
  usePauseGraphRun,
  useResumeGraphRun,
  useSetCrawlGraphRoot,
  useTriggerCrawlGraph,
  useUpdateCrawlGraph,
} from "@/hooks/use-crawl-graphs"
import type { CrawlGraphDetail, CrawlGraphNode } from "@/types/graphs"

export function CrawlGraphsPage({
  onNavigate,
}: {
  onNavigate: (href: string) => void
}) {
  const graphsQuery = useCrawlGraphs()
  const createGraph = useCreateCrawlGraph()

  const graphs = graphsQuery.data?.items ?? []

  const createPlan = () =>
    createGraph.mutate(undefined, {
      onSuccess: (graph) => onNavigate(`/crawls/plans/${graph.id}`),
    })

  return (
    <div className="flex min-h-0 w-full flex-col gap-4">
      <section className="min-h-0 overflow-hidden rounded-lg border bg-card/80">
        <div className="flex items-center justify-between gap-3 border-b px-4 py-3">
          <div className="flex items-center gap-2">
            <GitForkIcon className="size-4 text-muted-foreground" />
            <h1 className="font-medium">Crawl plans</h1>
            <Badge variant="outline">{graphsQuery.data?.total ?? 0}</Badge>
          </div>
          <Button disabled={createGraph.isPending} onClick={createPlan}>
            <PlusIcon />
            Create plan
          </Button>
        </div>
        <Table containerClassName="max-h-[calc(100svh-12rem)]">
          <TableHeader className="sticky top-0 z-10 bg-card">
            <TableRow>
              <TableHead>Plan</TableHead>
              <TableHead>Description</TableHead>
              <TableHead>Root</TableHead>
              <TableHead>Created</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {graphs.map((graph) => (
              <TableRow key={graph.id}>
                <TableCell>
                  <button
                    type="button"
                    className="font-medium text-foreground hover:underline"
                    onClick={() => onNavigate(`/crawls/plans/${graph.id}`)}
                  >
                    {graph.slug}
                  </button>
                  {graph.system_owned ? (
                    <Badge variant="secondary" className="ml-2">
                      System
                    </Badge>
                  ) : null}
                </TableCell>
                <TableCell className="max-w-md truncate text-muted-foreground">
                  {graph.description || "—"}
                </TableCell>
                <TableCell>
                  {graph.root_node_id ? "Configured" : "Not set"}
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {new Date(graph.created_at).toLocaleString()}
                </TableCell>
              </TableRow>
            ))}
            {!graphsQuery.isLoading && graphs.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={4}
                  className="h-32 text-center text-muted-foreground"
                >
                  Create the first crawl plan.
                </TableCell>
              </TableRow>
            ) : null}
          </TableBody>
        </Table>
      </section>
    </div>
  )
}

export function CrawlGraphDetailPage({
  graphId,
  onNavigate,
}: {
  graphId: string
  onNavigate: (href: string) => void
}) {
  const graphQuery = useCrawlGraph(graphId)
  const deleteGraph = useDeleteCrawlGraph()

  const removeGraph = () => {
    if (!window.confirm("Delete this crawl plan?")) return
    deleteGraph.mutate(graphId, {
      onSuccess: () => onNavigate("/crawls/plans"),
    })
  }

  if (graphQuery.isLoading) {
    return (
      <LoaderCircleIcon className="m-auto size-5 animate-spin text-muted-foreground" />
    )
  }
  if (!graphQuery.data) {
    return (
      <p className="m-auto text-sm text-muted-foreground">Plan not found.</p>
    )
  }
  return (
    <div className="w-full">
      <GraphDetail
        graph={graphQuery.data}
        onDelete={removeGraph}
        isDeleting={deleteGraph.isPending}
        onNavigate={onNavigate}
      />
    </div>
  )
}

function GraphDetail({
  graph,
  onDelete,
  isDeleting,
  onNavigate,
}: {
  graph: CrawlGraphDetail
  onDelete: () => void
  isDeleting: boolean
  onNavigate: (href: string) => void
}) {
  const setRoot = useSetCrawlGraphRoot(graph)
  const updatePlan = useUpdateCrawlGraph(graph.id)
  const activeRunsQuery = useActiveGraphRuns(graph.id)
  const activeRuns = useMemo(
    () => activeRunsQuery.data?.items ?? [],
    [activeRunsQuery.data?.items]
  )
  const [activeRunId, setActiveRunId] = useState<string | null>(null)
  const runQuery = useGraphRun(activeRunId)
  const cancelRun = useCancelGraphRun()
  const pauseRun = usePauseGraphRun()
  const resumeRun = useResumeGraphRun()
  const announcedRuns = useRef(new Set<string>())
  const [editingMetadata, setEditingMetadata] = useState<
    "slug" | "description" | null
  >(null)
  const [metadataDraft, setMetadataDraft] = useState("")

  const beginMetadataEdit = (field: "slug" | "description") => {
    if (graph.system_owned) return
    setMetadataDraft(field === "slug" ? graph.slug : (graph.description ?? ""))
    setEditingMetadata(field)
  }

  const saveMetadata = (field: "slug" | "description") => {
    setEditingMetadata(null)
    const slug = field === "slug" ? metadataDraft.trim() : graph.slug
    if (!slug) {
      toast.error("Plan slug is required.")
      return
    }
    const description =
      field === "description" ? metadataDraft.trim() : (graph.description ?? "")
    if (slug === graph.slug && description === (graph.description ?? "")) {
      return
    }
    updatePlan.mutate({
      slug,
      description,
      root_node_id: graph.root_node_id,
    })
  }

  useEffect(() => {
    if (activeRunId === null && activeRuns[0]) {
      // Backend-active execution is authoritative when entering the page.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setActiveRunId(activeRuns[0].id)
    }
  }, [activeRunId, activeRuns])

  useEffect(() => {
    const run = runQuery.data
    if (
      !run ||
      run.status === "queued" ||
      run.status === "running" ||
      run.status === "paused" ||
      announcedRuns.current.has(run.id)
    )
      return
    announcedRuns.current.add(run.id)
    toast.success(`Crawl run ${run.status.replaceAll("_", " ")}.`, {
      action: {
        label: "View metrics",
        onClick: () => onNavigate(`/crawls/metrics?run=${run.id}`),
      },
    })
    void activeRunsQuery.refetch().then(() => setActiveRunId(null))
  }, [activeRunsQuery, onNavigate, runQuery.data])
  return (
    <div className="flex min-h-0 flex-col gap-3">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b pb-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            {editingMetadata === "slug" ? (
              <Input
                autoFocus
                className="h-8 w-64 text-lg font-semibold"
                value={metadataDraft}
                aria-label="Plan slug"
                onChange={(event) =>
                  setMetadataDraft(
                    event.target.value
                      .toLowerCase()
                      .replace(/[^a-z0-9_-]/g, "-")
                  )
                }
                onBlur={() => saveMetadata("slug")}
                onKeyDown={(event) => {
                  if (event.key === "Enter") event.currentTarget.blur()
                  if (event.key === "Escape") setEditingMetadata(null)
                }}
              />
            ) : (
              <h2
                className={
                  graph.system_owned
                    ? "truncate text-xl font-semibold tracking-tight"
                    : "cursor-text truncate rounded-sm px-1 text-xl font-semibold tracking-tight outline-none hover:bg-muted/60 focus-visible:ring-2 focus-visible:ring-ring"
                }
                title={
                  graph.system_owned
                    ? graph.slug
                    : "Double-click to edit the plan slug"
                }
                tabIndex={graph.system_owned ? undefined : 0}
                onDoubleClick={() => beginMetadataEdit("slug")}
                onKeyDown={(event) => {
                  if (event.key === "Enter") beginMetadataEdit("slug")
                }}
              >
                {graph.slug}
              </h2>
            )}
            {graph.system_owned ? (
              <Badge variant="outline">System</Badge>
            ) : (
              <Select
                value={graph.root_node_id}
                onValueChange={(nodeId) => nodeId && setRoot.mutate(nodeId)}
                disabled={graph.nodes.length === 0 || setRoot.isPending}
              >
                <SelectTrigger className="w-48">
                  <span className="truncate">
                    Root:{" "}
                    {graph.nodes.find((node) => node.id === graph.root_node_id)
                      ?.name ?? "Not set"}
                  </span>
                </SelectTrigger>
                <SelectContent>
                  {graph.nodes.map((node) => (
                    <SelectItem key={node.id} value={node.id}>
                      {node.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>
          {editingMetadata === "description" ? (
            <Input
              autoFocus
              className="mt-1 h-8 w-[min(36rem,80vw)] text-sm"
              value={metadataDraft}
              aria-label="Plan description"
              placeholder="Add a description"
              onChange={(event) => setMetadataDraft(event.target.value)}
              onBlur={() => saveMetadata("description")}
              onKeyDown={(event) => {
                if (event.key === "Enter") event.currentTarget.blur()
                if (event.key === "Escape") setEditingMetadata(null)
              }}
            />
          ) : (
            <p
              className={
                graph.system_owned
                  ? "mt-1 text-sm text-muted-foreground"
                  : "mt-1 w-fit cursor-text rounded-sm px-1 text-sm text-muted-foreground outline-none hover:bg-muted/60 focus-visible:ring-2 focus-visible:ring-ring"
              }
              title={
                graph.system_owned
                  ? undefined
                  : "Double-click to edit the description"
              }
              tabIndex={graph.system_owned ? undefined : 0}
              onDoubleClick={() => beginMetadataEdit("description")}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  beginMetadataEdit("description")
                }
              }}
            >
              {graph.description || "No description"}
            </p>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          {activeRuns.length > 1 ? (
            <Select
              value={activeRunId}
              onValueChange={(runId) => runId && setActiveRunId(runId)}
            >
              <SelectTrigger className="w-44">
                <span className="truncate">Run {activeRunId?.slice(0, 8)}</span>
              </SelectTrigger>
              <SelectContent>
                {activeRuns.map((run) => (
                  <SelectItem key={run.id} value={run.id}>
                    {run.id.slice(0, 8)} · {run.request_count} requests
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : null}
          {activeRunId ? (
            <>
              {runQuery.data?.status === "paused" ? (
                <Button
                  variant="outline"
                  disabled={resumeRun.isPending}
                  onClick={() => resumeRun.mutate(activeRunId)}
                >
                  <PlayIcon />
                  Resume
                </Button>
              ) : (
                <Button
                  variant="outline"
                  disabled={pauseRun.isPending}
                  onClick={() => pauseRun.mutate(activeRunId)}
                >
                  <PauseIcon />
                  Pause
                </Button>
              )}
              <Button
                variant="outline"
                disabled={cancelRun.isPending}
                onClick={() => cancelRun.mutate(activeRunId)}
              >
                Cancel
              </Button>
            </>
          ) : null}
          {!graph.system_owned ? (
            <Button
              variant="ghost"
              className="text-destructive hover:text-destructive"
              disabled={isDeleting}
              onClick={onDelete}
            >
              <Trash2Icon />
              Delete
            </Button>
          ) : null}
        </div>
      </header>

      <RunPlanBar graph={graph} onStarted={setActiveRunId} />

      <GraphCanvas
        graph={graph}
        runId={activeRunId}
        readOnly={graph.system_owned}
      />
    </div>
  )
}

export function NodesCard({ graph }: { graph: CrawlGraphDetail }) {
  const createNode = useCreateCrawlGraphNode(graph.id)
  const deleteNode = useDeleteCrawlGraphNode(graph.id)
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")

  const submit = () => {
    if (!name.trim()) {
      toast.error("Node name is required.")
      return
    }
    createNode.mutate(
      { name: name.trim(), description: description.trim() },
      {
        onSuccess: () => {
          setName("")
          setDescription("")
        },
      }
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Nodes</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-2 sm:grid-cols-2">
          <Input
            value={name}
            placeholder="Node name"
            onChange={(event) => setName(event.target.value)}
          />
          <Input
            value={description}
            placeholder="Description"
            onChange={(event) => setDescription(event.target.value)}
          />
          <Button
            className="sm:col-span-2"
            disabled={createNode.isPending}
            onClick={submit}
          >
            <PlusIcon />
            Add node
          </Button>
        </div>

        <div className="space-y-2">
          {graph.nodes.map((node) => (
            <NodeRow
              key={node.id}
              node={node}
              isDeleting={deleteNode.isPending}
              onDelete={() => deleteNode.mutate(node.id)}
            />
          ))}
          {graph.nodes.length === 0 ? (
            <p className="py-4 text-center text-sm text-muted-foreground">
              Add a node to establish the plan root.
            </p>
          ) : null}
        </div>
      </CardContent>
    </Card>
  )
}

function NodeRow({
  node,
  isDeleting,
  onDelete,
}: {
  node: CrawlGraphNode
  isDeleting: boolean
  onDelete: () => void
}) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-md border p-3">
      <div className="min-w-0">
        <span className="truncate text-sm font-medium">{node.name}</span>
        <p className="truncate text-xs text-muted-foreground">
          {node.description || node.id}
        </p>
      </div>
      <Button
        size="icon-sm"
        variant="ghost"
        disabled={isDeleting}
        onClick={onDelete}
      >
        <Trash2Icon />
      </Button>
    </div>
  )
}

function RunPlanBar({
  graph,
  onStarted,
}: {
  graph: CrawlGraphDetail
  onStarted: (runId: string) => void
}) {
  const trigger = useTriggerCrawlGraph(graph.id)
  const [url, setUrl] = useState("")
  const [maxCrawls, setMaxCrawls] = useState("1000")
  const [maxRunDays, setMaxRunDays] = useState("7")

  const run = () => {
    const rootUrl = url.trim()
    if (!rootUrl) {
      toast.error("Enter a root URL.")
      return
    }
    const crawlBudget = Number(maxCrawls)
    if (!Number.isInteger(crawlBudget) || crawlBudget < 1) {
      toast.error("Maximum crawls must be at least one.")
      return
    }
    const runDays = Number(maxRunDays)
    if (!Number.isInteger(runDays) || runDays < 1 || runDays > 365) {
      toast.error("Maximum run duration must be between 1 and 365 days.")
      return
    }
    trigger.mutate(
      {
        url: rootUrl,
        max_crawls: crawlBudget,
        max_run_seconds: runDays * 24 * 60 * 60,
      },
      {
        onSuccess: (submission) => {
          toast.success(`Crawl run ${submission.run_id} queued.`)
          onStarted(submission.run_id)
          setUrl("")
        },
      }
    )
  }

  return (
    <form
      className="grid gap-2 rounded-lg border bg-card/50 p-2 shadow-sm md:grid-cols-[minmax(20rem,1fr)_9rem_8rem_auto]"
      onSubmit={(event) => {
        event.preventDefault()
        run()
      }}
    >
      <div className="relative min-w-0">
        <span className="pointer-events-none absolute top-1.5 left-3 z-10 text-[0.625rem] font-medium tracking-wide text-muted-foreground uppercase">
          Root URL
        </span>
        <Input
          className="h-12 pt-5 font-mono text-xs"
          value={url}
          placeholder="https://example.com/"
          aria-label="Root URL"
          onChange={(event) => setUrl(event.target.value)}
        />
      </div>
      <RunSettingField
        label="Max pages"
        value={maxCrawls}
        min={1}
        max={1_000_000}
        onChange={setMaxCrawls}
      />
      <RunSettingField
        label="Max days"
        value={maxRunDays}
        min={1}
        max={365}
        onChange={setMaxRunDays}
      />
      <Button
        className="h-12 px-5"
        type="submit"
        disabled={!graph.root_node_id || !url.trim() || trigger.isPending}
      >
        {trigger.isPending ? (
          <LoaderCircleIcon className="animate-spin" />
        ) : (
          <PlayIcon />
        )}
        Run
      </Button>
    </form>
  )
}

function RunSettingField({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string
  value: string
  min: number
  max: number
  onChange: (value: string) => void
}) {
  return (
    <div className="relative">
      <span className="pointer-events-none absolute top-1.5 left-3 z-10 flex items-center gap-1 text-[0.625rem] font-medium tracking-wide text-muted-foreground uppercase">
        <Settings2Icon className="size-2.5" />
        {label}
      </span>
      <Input
        className="h-12 pt-5 tabular-nums"
        type="number"
        min={min}
        max={max}
        value={value}
        aria-label={label}
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  )
}
