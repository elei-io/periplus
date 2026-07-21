import {
  GitForkIcon,
  LoaderCircleIcon,
  PlayIcon,
  PlusIcon,
  RefreshCwIcon,
  Trash2Icon,
} from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"
import { toast } from "sonner"

import { GraphCanvas } from "@/components/crawl-graph/graph-canvas"
import { SqlEditor } from "@/components/catalogue/sql-editor"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import {
  useCreateCrawlGraph,
  useCreateCrawlGraphEdge,
  useCreateCrawlGraphNode,
  useCrawlGraph,
  useCrawlGraphs,
  useDeleteCrawlGraph,
  useDeleteCrawlGraphEdge,
  useDeleteCrawlGraphNode,
  useActiveGraphRuns,
  useCancelGraphRun,
  useGraphRun,
  useSetCrawlGraphRoot,
  useTriggerCrawlGraph,
} from "@/hooks/use-crawl-graphs"
import type {
  CrawlGraphDetail,
  CrawlGraphEdge,
  CrawlGraphNode,
} from "@/types/graphs"

export function CrawlGraphsPage({
  onNavigate,
}: {
  onNavigate: (href: string) => void
}) {
  const graphsQuery = useCrawlGraphs()
  const createGraph = useCreateCrawlGraph()
  const [slug, setSlug] = useState("")
  const [description, setDescription] = useState("")

  const graphs = graphsQuery.data?.items ?? []

  const submitGraph = () => {
    const nextSlug = slug.trim()
    if (!nextSlug) {
      toast.error("Graph slug is required.")
      return
    }
    createGraph.mutate(
      { slug: nextSlug, description: description.trim() },
      {
        onSuccess: (graph) => {
          setSlug("")
          setDescription("")
          onNavigate(`/crawls/graphs/${graph.id}`)
        },
      }
    )
  }

  return (
    <div className="flex min-h-0 w-full flex-col gap-4">
      <section className="flex min-h-0 flex-col gap-3 rounded-lg border bg-card/80 p-4">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <GitForkIcon className="size-4 text-muted-foreground" />
            <h1 className="font-medium">Crawl Graphs</h1>
            <Badge variant="outline">{graphsQuery.data?.total ?? 0}</Badge>
          </div>
          <Button
            size="icon-sm"
            variant="ghost"
            disabled={graphsQuery.isFetching}
            onClick={() => void graphsQuery.refetch()}
          >
            <RefreshCwIcon />
          </Button>
        </div>

        <div className="grid gap-2 border-y py-4 md:grid-cols-[minmax(12rem,1fr)_minmax(16rem,2fr)_auto]">
          <Input
            value={slug}
            placeholder="graph-slug"
            onChange={(event) =>
              setSlug(
                event.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, "-")
              )
            }
          />
          <Input
            value={description}
            placeholder="Description"
            onChange={(event) => setDescription(event.target.value)}
          />
          <Button disabled={createGraph.isPending} onClick={submitGraph}>
            <PlusIcon />
            Create graph
          </Button>
        </div>

        <div className="grid min-h-0 gap-2 overflow-y-auto md:grid-cols-2 xl:grid-cols-3">
          {graphs.map((graph) => (
            <button
              key={graph.id}
              type="button"
              className="w-full rounded-md border px-4 py-3 text-left transition-colors hover:border-primary/30 hover:bg-muted"
              onClick={() => onNavigate(`/crawls/graphs/${graph.id}`)}
            >
              <span className="block truncate text-sm font-medium">
                {graph.slug}
                {graph.system_owned ? (
                  <Badge variant="secondary" className="ml-2">
                    System
                  </Badge>
                ) : null}
              </span>
              <span className="block truncate text-xs text-muted-foreground">
                Created {new Date(graph.created_at).toLocaleDateString()}
              </span>
            </button>
          ))}
          {!graphsQuery.isLoading && graphs.length === 0 ? (
            <p className="px-2 py-6 text-center text-sm text-muted-foreground">
              Create the first crawl graph.
            </p>
          ) : null}
        </div>
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
    if (!window.confirm("Delete this crawl graph?")) return
    deleteGraph.mutate(graphId, {
      onSuccess: () => onNavigate("/crawls/graphs"),
    })
  }

  if (graphQuery.isLoading) {
    return (
      <LoaderCircleIcon className="m-auto size-5 animate-spin text-muted-foreground" />
    )
  }
  if (!graphQuery.data) {
    return (
      <p className="m-auto text-sm text-muted-foreground">Graph not found.</p>
    )
  }
  return (
    <div className="w-full">
      <GraphDetail
        graph={graphQuery.data}
        isRefreshing={graphQuery.isFetching}
        onRefresh={() => void graphQuery.refetch()}
        onDelete={removeGraph}
        isDeleting={deleteGraph.isPending}
        onNavigate={onNavigate}
      />
    </div>
  )
}

function GraphDetail({
  graph,
  isRefreshing,
  onRefresh,
  onDelete,
  isDeleting,
  onNavigate,
}: {
  graph: CrawlGraphDetail
  isRefreshing: boolean
  onRefresh: () => void
  onDelete: () => void
  isDeleting: boolean
  onNavigate: (href: string) => void
}) {
  const setRoot = useSetCrawlGraphRoot(graph)
  const activeRunsQuery = useActiveGraphRuns(graph.id)
  const activeRuns = useMemo(
    () => activeRunsQuery.data?.items ?? [],
    [activeRunsQuery.data?.items]
  )
  const [activeRunId, setActiveRunId] = useState<string | null>(null)
  const runQuery = useGraphRun(activeRunId)
  const cancelRun = useCancelGraphRun()
  const announcedRuns = useRef(new Set<string>())

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
      announcedRuns.current.has(run.id)
    )
      return
    announcedRuns.current.add(run.id)
    toast.success(`Graph run ${run.status.replaceAll("_", " ")}.`, {
      action: {
        label: "View metrics",
        onClick: () => onNavigate(`/crawls/metrics?run=${run.id}`),
      },
    })
    void activeRunsQuery.refetch().then(() => setActiveRunId(null))
  }, [activeRunsQuery, onNavigate, runQuery.data])
  return (
    <div className="flex min-h-0 flex-col gap-4">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b pb-4">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-xl font-semibold tracking-tight">
              {graph.slug}
            </h2>
            <Badge variant="secondary">
              {graph.root_node_id ? "Root set" : "No root"}
            </Badge>
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
          <p className="mt-1 text-sm text-muted-foreground">
            {graph.description || "No description"}
          </p>
          <p className="mt-1 font-mono text-xs text-muted-foreground">
            {graph.id}
          </p>
        </div>
        <div className="flex gap-2">
          <RunGraphButton graph={graph} onStarted={setActiveRunId} />
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
            <Button
              variant="outline"
              disabled={cancelRun.isPending}
              onClick={() => cancelRun.mutate(activeRunId)}
            >
              Cancel
            </Button>
          ) : null}
          <Button variant="outline" disabled={isRefreshing} onClick={onRefresh}>
            <RefreshCwIcon />
            Refresh
          </Button>
          {!graph.system_owned ? (
            <Button
              variant="destructive"
              disabled={isDeleting}
              onClick={onDelete}
            >
              <Trash2Icon />
              Delete
            </Button>
          ) : null}
        </div>
      </header>

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
              Add a node to establish the graph root.
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

export function EdgesCard({ graph }: { graph: CrawlGraphDetail }) {
  const createEdge = useCreateCrawlGraphEdge(graph.id)
  const deleteEdge = useDeleteCrawlGraphEdge(graph.id)
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [sourceId, setSourceId] = useState("")
  const [targetId, setTargetId] = useState("")
  const [sql, setSql] = useState(
    "SELECT target_url AS url\nFROM edge.page_links\nWHERE crawl_id = $crawl_id\n  AND relation_kind <> 'external'\nLIMIT 100000"
  )

  const nodeOptions = useMemo(
    () => new Map(graph.nodes.map((node) => [node.id, node.name])),
    [graph.nodes]
  )

  /* eslint-disable react-hooks/set-state-in-effect -- defaults follow server graph membership */
  useEffect(() => {
    if (!nodeOptions.has(sourceId)) {
      setSourceId(graph.nodes[0]?.id ?? "")
    }
    if (!nodeOptions.has(targetId)) {
      setTargetId(graph.nodes[0]?.id ?? "")
    }
  }, [graph.nodes, nodeOptions, sourceId, targetId])
  /* eslint-enable react-hooks/set-state-in-effect */

  const submit = () => {
    if (!name.trim() || !sourceId || !targetId || !sql.trim()) {
      toast.error("Edge name, endpoints, and SQL are required.")
      return
    }
    createEdge.mutate(
      {
        name: name.trim(),
        description: description.trim(),
        source_node_id: sourceId,
        target_node_id: targetId,
        sql: sql.trim(),
      },
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
        <CardTitle>SQL edges</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-2 sm:grid-cols-2">
          <Input
            value={name}
            placeholder="Edge name"
            onChange={(event) => setName(event.target.value)}
          />
          <Input
            value={description}
            placeholder="Description"
            onChange={(event) => setDescription(event.target.value)}
          />
          <NodeSelect
            label="Source"
            nodes={graph.nodes}
            value={sourceId}
            onChange={setSourceId}
          />
          <NodeSelect
            label="Target"
            nodes={graph.nodes}
            value={targetId}
            onChange={setTargetId}
          />
          <Textarea
            className="min-h-32 font-mono text-xs sm:col-span-2"
            value={sql}
            onChange={(event) => setSql(event.target.value)}
          />
          <Button
            className="sm:col-span-2"
            disabled={createEdge.isPending || graph.nodes.length === 0}
            onClick={submit}
          >
            <PlusIcon />
            Add edge
          </Button>
        </div>

        <div className="space-y-2">
          {graph.edges.map((edge) => (
            <EdgeRow
              key={edge.id}
              edge={edge}
              nodeNames={nodeOptions}
              isDeleting={deleteEdge.isPending}
              onDelete={() => deleteEdge.mutate(edge.id)}
            />
          ))}
          {graph.edges.length === 0 ? (
            <p className="py-4 text-center text-sm text-muted-foreground">
              Edges turn each ready crawl into URL inputs for another node.
            </p>
          ) : null}
        </div>
      </CardContent>
    </Card>
  )
}

function NodeSelect({
  label,
  nodes,
  value,
  onChange,
}: {
  label: string
  nodes: CrawlGraphNode[]
  value: string
  onChange: (value: string) => void
}) {
  return (
    <Select
      value={value || null}
      onValueChange={(nextValue) => nextValue && onChange(nextValue)}
    >
      <SelectTrigger className="w-full">
        <span>
          {label}: {nodes.find((node) => node.id === value)?.name ?? "Select"}
        </span>
      </SelectTrigger>
      <SelectContent>
        {nodes.map((node) => (
          <SelectItem key={node.id} value={node.id}>
            {node.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

function EdgeRow({
  edge,
  nodeNames,
  isDeleting,
  onDelete,
}: {
  edge: CrawlGraphEdge
  nodeNames: Map<string, string>
  isDeleting: boolean
  onDelete: () => void
}) {
  const selfEdge = edge.source_node_id === edge.target_node_id
  return (
    <div className="rounded-md border p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium">{edge.name}</span>
            {selfEdge ? <Badge variant="secondary">Self-edge</Badge> : null}
          </div>
          <p className="text-xs text-muted-foreground">
            {nodeNames.get(edge.source_node_id) ?? edge.source_node_id} →{" "}
            {nodeNames.get(edge.target_node_id) ?? edge.target_node_id}
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
      <div className="mt-2 overflow-hidden rounded border bg-card/50">
        <SqlEditor
          value={edge.sql}
          readOnly
          height={`${Math.max(80, Math.min(128, edge.sql.split("\n").length * 20 + 28))}px`}
          ariaLabel={`Read-only SQL for edge ${edge.name}`}
        />
      </div>
    </div>
  )
}

function RunGraphButton({
  graph,
  onStarted,
}: {
  graph: CrawlGraphDetail
  onStarted: (runId: string) => void
}) {
  const trigger = useTriggerCrawlGraph(graph.id)
  const [urlsText, setUrlsText] = useState("")
  const [maxCrawls, setMaxCrawls] = useState("1000")
  const [open, setOpen] = useState(false)

  const run = () => {
    const urls = urlsText
      .split("\n")
      .map((value) => value.trim())
      .filter(Boolean)
    if (urls.length === 0) {
      toast.error("Enter at least one URL.")
      return
    }
    const crawlBudget = Number(maxCrawls)
    if (!Number.isInteger(crawlBudget) || crawlBudget < urls.length) {
      toast.error("Maximum crawls must be at least the number of root URLs.")
      return
    }
    trigger.mutate(
      { urls, max_crawls: crawlBudget },
      {
        onSuccess: (submission) => {
          toast.success(`Graph run ${submission.run_id} queued.`)
          onStarted(submission.run_id)
          setOpen(false)
          setUrlsText("")
        },
      }
    )
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button disabled={!graph.root_node_id} onClick={() => setOpen(true)}>
        <PlayIcon />
        Run graph
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Run graph</DialogTitle>
          <DialogDescription>
            Enter one URL per line. Each URL is offered to the root node.
          </DialogDescription>
        </DialogHeader>
        <Textarea
          className="min-h-40 font-mono text-xs"
          value={urlsText}
          placeholder={"https://example.com/a\nhttps://example.com/b"}
          onChange={(event) => setUrlsText(event.target.value)}
        />
        <div className="space-y-1.5">
          <p className="text-sm font-medium">Maximum crawls</p>
          <Input
            min={1}
            max={1_000_000}
            type="number"
            value={maxCrawls}
            onChange={(event) => setMaxCrawls(event.target.value)}
          />
          <p className="text-xs text-muted-foreground">
            Stops admitting new URLs when this run reaches its budget.
          </p>
        </div>
        <DialogFooter>
          <Button disabled={trigger.isPending} onClick={run}>
            {trigger.isPending ? (
              <LoaderCircleIcon className="animate-spin" />
            ) : (
              <PlayIcon />
            )}
            Start run
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
