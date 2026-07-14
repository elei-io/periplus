import {
  Background,
  BaseEdge,
  EdgeLabelRenderer,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  getBezierPath,
  useNodesState,
  type Connection,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
  type OnConnectEnd,
  type ReactFlowInstance,
} from "@xyflow/react"
import "@xyflow/react/dist/style.css"
import { CopyIcon, PencilIcon, Trash2Icon } from "lucide-react"
import { AnimatePresence, motion } from "motion/react"
import { useCallback, useEffect, useMemo, useState } from "react"

import { SqlEditor } from "@/components/catalogue/sql-editor"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@/components/ui/context-menu"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/select"
import {
  useCreateCrawlGraphEdge,
  useCreateCrawlGraphNode,
  useDeleteCrawlGraphEdge,
  useDeleteCrawlGraphNode,
  useSetCrawlGraphRoot,
  useUpdateCrawlGraphEdge,
  useUpdateCrawlGraphNode,
  useUpdateCrawlGraphNodePosition,
} from "@/hooks/use-crawl-graphs"
import { GraphProgressProvider, useEdgeProgress, useGraphProgressConnection, useNodeProgress } from "@/hooks/use-graph-progress"
import type { CrawlGraphDetail, CrawlGraphEdge, CrawlGraphNode, EdgeDedupeMode } from "@/types/graphs"

const DEFAULT_EDGE_SQL = `SELECT url
FROM nav.links
WHERE crawl_id = $crawl_id
LIMIT 100000`

type CrawlNodeData = {
  node: CrawlGraphNode
  save: (node: CrawlGraphNode, values: NodeValues) => void
  remove: (id: string) => void
  copy: (node: CrawlGraphNode) => void
  runId: string | null
  isRoot: boolean
  setRoot: (id: string) => void
}
type CrawlNode = Node<CrawlNodeData, "crawlNode">
type NodeValues = { name: string; description: string }

type CrawlEdgeData = {
  edge: CrawlGraphEdge
  save: (edge: CrawlGraphEdge, values: EdgeValues) => void
  remove: (id: string) => void
  copy: (edge: CrawlGraphEdge) => void
  edit: (id: string) => void
  runId: string | null
}
type CrawlFlowEdge = Edge<CrawlEdgeData, "crawlEdge">
type EdgeValues = { name: string; description: string; sql: string; dedupe_mode: EdgeDedupeMode }

const nodeTypes = { crawlNode: CrawlNodeCard }
const edgeTypes = { crawlEdge: CrawlEdgeEditor }

export function GraphCanvas({ graph, runId }: { graph: CrawlGraphDetail; runId: string | null }) {
  return (
    <GraphProgressProvider runId={runId}>
      <GraphCanvasContent graph={graph} runId={runId} />
    </GraphProgressProvider>
  )
}

function GraphCanvasContent({ graph, runId }: { graph: CrawlGraphDetail; runId: string | null }) {
  const connectionState = useGraphProgressConnection()
  const createNode = useCreateCrawlGraphNode(graph.id)
  const updateNode = useUpdateCrawlGraphNode(graph.id)
  const updateNodePosition = useUpdateCrawlGraphNodePosition(graph.id)
  const deleteNode = useDeleteCrawlGraphNode(graph.id)
  const createEdge = useCreateCrawlGraphEdge(graph.id)
  const updateEdge = useUpdateCrawlGraphEdge(graph.id)
  const deleteEdge = useDeleteCrawlGraphEdge(graph.id)
  const setRoot = useSetCrawlGraphRoot(graph)
  const [instance, setInstance] = useState<ReactFlowInstance<CrawlNode, CrawlFlowEdge> | null>(null)
  const [editingEdgeId, setEditingEdgeId] = useState<string | null>(null)

  const saveNode = useCallback((node: CrawlGraphNode, values: NodeValues) => {
    updateNode.mutate({ id: node.id, ...values })
  }, [updateNode])
  const saveEdge = useCallback((edge: CrawlGraphEdge, values: EdgeValues) => {
    updateEdge.mutate({
      id: edge.id,
      source_node_id: edge.source_node_id,
      target_node_id: edge.target_node_id,
      ...values,
    })
  }, [updateEdge])
  const copyNode = useCallback((node: CrawlGraphNode) => {
    createNode.mutate({
      name: `${node.name} copy ${graph.nodes.length + 1}`,
      description: node.description ?? "",
    })
  }, [createNode, graph.nodes.length])
  const copyEdge = useCallback((edge: CrawlGraphEdge) => {
    createEdge.mutate({
      name: `${edge.name} copy ${graph.edges.length + 1}`,
      description: edge.description ?? "",
      source_node_id: edge.source_node_id,
      target_node_id: edge.target_node_id,
      sql: edge.sql,
      dedupe_mode: edge.dedupe_mode,
    })
  }, [createEdge, graph.edges.length])

  const projectedNodes = useMemo<CrawlNode[]>(() => graph.nodes.map((node, index) => ({
    id: node.id,
    type: "crawlNode",
    position: {
      x: node.position_x ?? 100 + (index % 3) * 640,
      y: node.position_y ?? 100 + Math.floor(index / 3) * 280,
    },
    data: { node, save: saveNode, remove: (id) => deleteNode.mutate(id), copy: copyNode, runId, isRoot: graph.root_node_id === node.id, setRoot: (id) => setRoot.mutate(id) },
  })), [copyNode, deleteNode, graph.nodes, graph.root_node_id, runId, saveNode, setRoot])

  const projectedEdges = useMemo<CrawlFlowEdge[]>(() => graph.edges.map((edge) => ({
    id: edge.id,
    type: "crawlEdge",
    source: edge.source_node_id,
    target: edge.target_node_id,
    markerEnd: { type: MarkerType.ArrowClosed },
    data: { edge, save: saveEdge, remove: (id) => deleteEdge.mutate(id), copy: copyEdge, edit: setEditingEdgeId, runId },
  })), [copyEdge, deleteEdge, graph.edges, runId, saveEdge])
  const [nodes, setNodes, onNodesChange] = useNodesState(projectedNodes)
  const nodesRevision = `${runId ?? "no-run"}|${graph.nodes
    .map((node) => `${node.id}:${node.name}:${node.description}:${node.used_at}:${graph.root_node_id === node.id}`)
    .join("|")}`

  useEffect(() => {
    setNodes((current) => projectedNodes.map((node) => ({
      ...node,
      position: current.find((item) => item.id === node.id)?.position ?? node.position,
    })))
    // Callback identities in node data do not represent graph-definition changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodesRevision, setNodes])
  useEffect(() => {
    if (!instance || graph.nodes.length === 0) return
    const frame = window.requestAnimationFrame(() => {
      void instance.fitView({ padding: 0.25, duration: 200 })
    })
    return () => window.cancelAnimationFrame(frame)
  }, [graph.nodes.length, instance])

  const addEdge = useCallback(async (source: string, target: string) => {
    await createEdge.mutateAsync({
      name: `Edge ${graph.edges.length + 1}`,
      description: "",
      source_node_id: source,
      target_node_id: target,
      sql: DEFAULT_EDGE_SQL,
      dedupe_mode: "graph",
    })
  }, [createEdge, graph.edges.length])

  const onConnect = useCallback((connection: Connection) => {
    if (connection.source && connection.target) void addEdge(connection.source, connection.target)
  }, [addEdge])

  const addStartNode = useCallback(() => {
    createNode.mutate({
      name: "Start",
      description: "Receives graph trigger URLs.",
    })
  }, [createNode])

  const onConnectEnd: OnConnectEnd = useCallback((event, state) => {
    if (state.isValid || !state.fromNode || !instance) return
    const point = "changedTouches" in event ? event.changedTouches[0] : event
    if (!point) return
    const position = instance.screenToFlowPosition({ x: point.clientX, y: point.clientY })
    const sourceId = state.fromNode.id
    void (async () => {
      const node = await createNode.mutateAsync({
        name: `Node ${graph.nodes.length + 1}`,
        description: "",
      })
      setNodes((current) => [...current, {
        id: node.id,
        type: "crawlNode",
        position,
        data: { node, save: saveNode, remove: (id: string) => deleteNode.mutate(id), copy: copyNode, runId, isRoot: false, setRoot: (id: string) => setRoot.mutate(id) },
      }])
      await addEdge(sourceId, node.id)
    })()
  }, [addEdge, copyNode, createNode, deleteNode, graph.nodes.length, instance, runId, saveNode, setNodes, setRoot])

  return (
    <div className="relative h-[calc(100svh-14rem)] min-h-[36rem] w-full overflow-hidden rounded-lg border bg-card/30">
      <ReactFlow<CrawlNode, CrawlFlowEdge>
        nodes={nodes}
        edges={projectedEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onInit={setInstance}
        onConnect={onConnect}
        onConnectEnd={onConnectEnd}
        onNodesChange={onNodesChange}
        onNodeDragStop={(_event, node) => updateNodePosition.mutate({
          nodeId: node.id,
          x: node.position.x,
          y: node.position.y,
        })}
        onEdgeClick={(_event, edge) => setEditingEdgeId(edge.id)}
        onEdgeContextMenu={(event, edge) => {
          event.preventDefault()
          setEditingEdgeId(edge.id)
        }}
        fitView
        fitViewOptions={{ padding: 0.25 }}
        minZoom={0.2}
        maxZoom={2}
        defaultEdgeOptions={{ markerEnd: { type: MarkerType.ArrowClosed } }}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={24} size={1} />
      </ReactFlow>
      {connectionState === "reconnecting" || connectionState === "stale" ? (
        <div className="pointer-events-none absolute top-3 right-3 z-10 rounded-full border bg-background/95 px-2.5 py-1 text-[0.6875rem] text-amber-500 shadow-sm">
          {connectionState === "stale" ? "Progress stale" : "Progress reconnecting"}
        </div>
      ) : null}
      {editingEdgeId ? (
        <EdgeEditDialog
          key={editingEdgeId}
          edge={graph.edges.find((edge) => edge.id === editingEdgeId) ?? null}
          open
          onOpenChange={(open) => !open && setEditingEdgeId(null)}
          onSave={saveEdge}
        />
      ) : null}
      {graph.nodes.length === 0 ? (
        <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center">
          <div className="pointer-events-auto flex max-w-sm flex-col items-center rounded-xl border bg-background/95 px-8 py-7 text-center shadow-xl backdrop-blur-sm">
            <h3 className="text-base font-semibold">Add your first node to get started</h3>
            <p className="mt-2 text-sm text-muted-foreground">
              The first node receives the URLs supplied when this graph is triggered.
            </p>
            <Button
              className="mt-5"
              disabled={createNode.isPending}
              onClick={addStartNode}
            >
              Add first node
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  )
}

function CrawlNodeCard({ data }: NodeProps<CrawlNode>) {
  const { node } = data
  const progress = useNodeProgress(data.runId, node.id)
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(node.name)
  const [description, setDescription] = useState(node.description ?? "")
  const working = Boolean(progress && (
    progress.queued + progress.crawling + progress.awaiting_navigation +
    progress.evaluating_edges > 0
  ))
  return (
    <>
      <ContextMenu>
        <ContextMenuTrigger render={<div />} className="relative min-w-60 rounded-lg border bg-card text-card-foreground shadow-lg">
          {working ? (
            <motion.div
              className="pointer-events-none absolute -inset-px rounded-lg border border-primary/50"
              animate={{ opacity: [0.25, 0.7, 0.25] }}
              transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut" }}
            />
          ) : null}
          <div className="pointer-events-none absolute inset-x-0 bottom-[calc(100%+0.65rem)] h-8">
            <AnimatePresence mode="popLayout">
              {progress?.activity[0] ? (
                <motion.div
                  key={`${progress.activity[0].request_id}:${progress.activity[0].status}:${progress.activity[0].updated_at}`}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -12 }}
                  transition={{ duration: 0.22, ease: "easeOut" }}
                  className="absolute inset-x-0 flex h-8 items-center gap-2 rounded-lg border bg-background/95 px-3 text-xs shadow-md backdrop-blur-sm"
                >
                  <span className={`size-1.5 shrink-0 rounded-full ${progress.activity[0].status === "completed" ? "bg-emerald-500" : progress.activity[0].status === "failed" ? "bg-destructive" : "animate-pulse bg-primary"}`} />
                  <span className="font-medium">{activityLabel(progress.activity[0].status)}</span>
                  <span className="truncate text-muted-foreground">{displayUrl(progress.activity[0].url)}</span>
                </motion.div>
              ) : null}
            </AnimatePresence>
          </div>
          <Handle type="target" position={Position.Left} />
          <div className="flex w-full items-center gap-2 p-3 text-left">
            <p className="min-w-0 flex-1 truncate font-medium">{node.name}</p>
            {data.isRoot ? <Badge variant="secondary">Root</Badge> : null}
          </div>
          <Handle type="source" position={Position.Right} />
        </ContextMenuTrigger>
        <ContextMenuContent>
          <ContextMenuItem disabled={Boolean(node.used_at)} onClick={() => setEditing(true)}><PencilIcon />Edit</ContextMenuItem>
          {!data.isRoot ? <ContextMenuItem onClick={() => data.setRoot(node.id)}>Set as root</ContextMenuItem> : null}
          <ContextMenuItem onClick={() => data.copy(node)}><CopyIcon />Copy</ContextMenuItem>
          <ContextMenuSeparator />
          <ContextMenuItem variant="destructive" onClick={() => data.remove(node.id)}><Trash2Icon />Delete</ContextMenuItem>
        </ContextMenuContent>
      </ContextMenu>
      <Dialog open={editing} onOpenChange={setEditing}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit node</DialogTitle>
            <DialogDescription>Configure how this node receives crawl inputs.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
          <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="Node name" />
          <Input value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Description" />
          </div>
          <DialogFooter>
            <Button onClick={() => { data.save(node, { name, description }); setEditing(false) }}>Save changes</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

function activityLabel(status: string) {
  return {
    queued: "Queued",
    crawling: "Crawling",
    awaiting_navigation: "Materializing",
    evaluating_edges: "Following links",
    completed: "Crawled",
    failed: "Failed",
    cancelled: "Cancelled",
  }[status] ?? status
}

function displayUrl(value: string) {
  try {
    const url = new URL(value)
    return `${url.hostname}${url.pathname === "/" ? "" : url.pathname}${url.search}`
  } catch {
    return value
  }
}

function CrawlEdgeEditor(props: EdgeProps<CrawlFlowEdge>) {
  const [path, labelX, labelY] = getBezierPath(props)
  const edge = props.data!.edge
  const progress = useEdgeProgress(props.data!.runId, edge.id)
  return (
    <>
      <BaseEdge
        path={path}
        markerEnd={props.markerEnd}
        interactionWidth={24}
        className="cursor-pointer"
      />
      <EdgeLabelRenderer>
        <div className="nodrag nopan absolute" style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}>
          <ContextMenu>
            <ContextMenuTrigger
              className="cursor-pointer rounded-full border bg-background px-3 py-1 text-xs font-medium shadow-sm"
              onClick={() => props.data!.edit(edge.id)}
            >
              {edge.name}
            </ContextMenuTrigger>
            <ContextMenuContent>
              <ContextMenuItem onClick={() => props.data!.edit(edge.id)}><PencilIcon />Edit</ContextMenuItem>
              <ContextMenuItem onClick={() => props.data!.copy(edge)}><CopyIcon />Copy</ContextMenuItem>
              <ContextMenuSeparator />
              <ContextMenuItem variant="destructive" onClick={() => props.data!.remove(edge.id)}><Trash2Icon />Delete</ContextMenuItem>
            </ContextMenuContent>
          </ContextMenu>
          <div className="pointer-events-none absolute top-[calc(100%+0.5rem)] left-1/2 h-7 -translate-x-1/2">
            <AnimatePresence mode="popLayout">
              {progress ? (
                <motion.div
                  key={`${progress.urls_selected}:${progress.urls_admitted}:${progress.urls_deduplicated}:${progress.evaluations_running}:${progress.evaluations_completed}`}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -10 }}
                  transition={{ duration: 0.22, ease: "easeOut" }}
                  className="absolute left-1/2 flex h-7 w-max -translate-x-1/2 items-center gap-1.5 rounded-lg border bg-background/95 px-2.5 text-[0.6875rem] tabular-nums text-muted-foreground shadow-sm backdrop-blur-sm"
                >
                  <span>{progress.urls_admitted} passed</span>
                  {progress.urls_deduplicated ? <span>· {progress.urls_deduplicated} duplicate</span> : null}
                  {progress.evaluations_running ? <span>· evaluating</span> : null}
                </motion.div>
              ) : null}
            </AnimatePresence>
          </div>
        </div>
      </EdgeLabelRenderer>
    </>
  )
}

function EdgeEditDialog({
  edge,
  open,
  onOpenChange,
  onSave,
}: {
  edge: CrawlGraphEdge | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onSave: (edge: CrawlGraphEdge, values: EdgeValues) => void
}) {
  const [name, setName] = useState(edge?.name ?? "")
  const [description, setDescription] = useState(edge?.description ?? "")
  const [sql, setSql] = useState(edge?.sql ?? "")
  const [dedupeMode, setDedupeMode] = useState<EdgeDedupeMode>(edge?.dedupe_mode ?? "graph")
  if (!edge) return null
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Edit edge</DialogTitle>
          <DialogDescription>The SQL must return URLs derived from the source crawl.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="Edge name" />
          <Input value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Description" />
          <Select
            value={dedupeMode}
            onValueChange={(value) => value && setDedupeMode(value as EdgeDedupeMode)}
            disabled={Boolean(edge.used_at)}
          >
            <SelectTrigger className="w-full" aria-label="URL deduplication scope">
              <span>Deduplicate per {dedupeMode}</span>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="graph">Graph run</SelectItem>
              <SelectItem value="crawl">Source crawl</SelectItem>
              <SelectItem value="document">Source document</SelectItem>
            </SelectContent>
          </Select>
          <div className="overflow-hidden rounded-md border">
            <SqlEditor value={sql} onChange={setSql} readOnly={Boolean(edge.used_at)} height="240px" ariaLabel="Edge SQL" enableCssSelect />
          </div>
        </div>
        <DialogFooter>
          <Button disabled={Boolean(edge.used_at)} onClick={() => { onSave(edge, { name, description, sql, dedupe_mode: dedupeMode }); onOpenChange(false) }}>
            Save changes
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
