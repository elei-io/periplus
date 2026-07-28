import { CopyIcon, PencilIcon, PlusIcon, Trash2Icon } from "lucide-react"
import { useState } from "react"

import { Badge } from "@/components/ui/badge"
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
import {
  useCreateCrawlGraphNode,
  useDeleteCrawlGraphNode,
  useSetCrawlGraphRoot,
  useUpdateCrawlGraphNode,
} from "@/hooks/use-crawl-graphs"
import {
  GraphProgressProvider,
  useNodeProgress,
} from "@/hooks/use-graph-progress"
import type { CrawlGraphDetail, CrawlGraphNode } from "@/types/graphs"

export function GraphCanvas({
  graph,
  runId,
  readOnly = false,
}: {
  graph: CrawlGraphDetail
  runId: string | null
  readOnly?: boolean
}) {
  return (
    <GraphProgressProvider runId={runId}>
      <NodeCanvas graph={graph} runId={runId} readOnly={readOnly} />
    </GraphProgressProvider>
  )
}

function NodeCanvas({
  graph,
  runId,
  readOnly,
}: {
  graph: CrawlGraphDetail
  runId: string | null
  readOnly: boolean
}) {
  const createNode = useCreateCrawlGraphNode(graph.id)
  const updateNode = useUpdateCrawlGraphNode(graph.id)
  const deleteNode = useDeleteCrawlGraphNode(graph.id)
  const setRoot = useSetCrawlGraphRoot(graph)

  return (
    <div className="flex min-h-80 flex-1 flex-col rounded-lg border bg-muted/20 p-4">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <p className="text-sm font-medium">Acquisition node</p>
          <p className="text-xs text-muted-foreground">
            This cutoff runs one root crawl node. SQL edges return with the C compiler.
          </p>
        </div>
        <Button
          size="sm"
          disabled={readOnly || graph.nodes.length > 0 || createNode.isPending}
          onClick={() =>
            createNode.mutate({
              name: "crawl",
              description: "Root page acquisition",
            })
          }
        >
          <PlusIcon />
          Add root
        </Button>
      </div>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {graph.nodes.map((node) => (
          <NodeCard
            key={node.id}
            node={node}
            runId={runId}
            isRoot={graph.root_node_id === node.id}
            readOnly={readOnly}
            onSave={(name, description) =>
              updateNode.mutate({ id: node.id, name, description })
            }
            onCopy={() =>
              createNode.mutate({
                name: `${node.name} copy`,
                description: node.description ?? "",
              })
            }
            onDelete={() => deleteNode.mutate(node.id)}
            onSetRoot={() => setRoot.mutate(node.id)}
          />
        ))}
      </div>
      {graph.nodes.length === 0 ? (
        <p className="my-auto text-center text-sm text-muted-foreground">
          Add one root node to create a runnable crawl graph.
        </p>
      ) : null}
    </div>
  )
}

function NodeCard({
  node,
  runId,
  isRoot,
  readOnly,
  onSave,
  onCopy,
  onDelete,
  onSetRoot,
}: {
  node: CrawlGraphNode
  runId: string | null
  isRoot: boolean
  readOnly: boolean
  onSave: (name: string, description: string) => void
  onCopy: () => void
  onDelete: () => void
  onSetRoot: () => void
}) {
  const progress = useNodeProgress(runId, node.id)
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(node.name)
  const [description, setDescription] = useState(node.description ?? "")

  return (
    <>
      <article className="rounded-lg border bg-background p-4 shadow-sm">
        <div className="flex items-start gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <p className="truncate font-medium">{node.name}</p>
              {isRoot ? <Badge variant="secondary">Root</Badge> : null}
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              {node.description || "Page acquisition"}
            </p>
          </div>
          {!readOnly ? (
            <div className="flex">
              <Button size="icon-sm" variant="ghost" onClick={() => setEditing(true)}>
                <PencilIcon />
              </Button>
              <Button size="icon-sm" variant="ghost" onClick={onCopy}>
                <CopyIcon />
              </Button>
              <Button size="icon-sm" variant="ghost" onClick={onDelete}>
                <Trash2Icon />
              </Button>
            </div>
          ) : null}
        </div>
        {progress ? (
          <div className="mt-3 flex gap-3 text-xs text-muted-foreground">
            <span>{progress.completed.toLocaleString()} completed</span>
            <span>{progress.failed.toLocaleString()} failed</span>
            <span>
              {(
                progress.queued +
                progress.crawling +
                progress.awaiting_navigation +
                progress.evaluating_edges
              ).toLocaleString()}{" "}
              active
            </span>
          </div>
        ) : null}
        {!readOnly && !isRoot ? (
          <Button className="mt-3" size="sm" variant="outline" onClick={onSetRoot}>
            Set as root
          </Button>
        ) : null}
      </article>
      <Dialog open={editing} onOpenChange={setEditing}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit node</DialogTitle>
            <DialogDescription>Update this acquisition node.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <Input value={name} onChange={(event) => setName(event.target.value)} />
            <Input
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </div>
          <DialogFooter>
            <Button
              onClick={() => {
                onSave(name.trim(), description.trim())
                setEditing(false)
              }}
            >
              Save changes
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
