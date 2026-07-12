import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
import type {
  CrawlGraphDetail,
  CrawlGraphEdge,
  CrawlGraphListResponse,
  CrawlGraphNode,
  GraphRunSubmission,
  GraphRunListResponse,
  GraphRunRecord,
} from "@/types/graphs"

const graphsKey = ["crawl-graphs"] as const

async function jsonResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw await apiErrorFromResponse(response)
  }
  return (await response.json()) as T
}

export function useCrawlGraphs() {
  return useQuery({
    queryKey: graphsKey,
    queryFn: async () =>
      jsonResponse<CrawlGraphListResponse>(
        await fetch(apiUrl("/crawl-graphs/"))
      ),
  })
}

export function useCrawlGraph(graphId: string | null) {
  return useQuery({
    queryKey: [...graphsKey, graphId],
    enabled: graphId !== null,
    queryFn: async () =>
      jsonResponse<CrawlGraphDetail>(
        await fetch(apiUrl(`/crawl-graphs/${graphId}`))
      ),
  })
}

function useGraphMutation<TVariables, TResult>(
  mutationFn: (variables: TVariables) => Promise<TResult>
) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: graphsKey })
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useCreateCrawlGraph() {
  return useGraphMutation<
    { name: string; description: string },
    CrawlGraphDetail
  >(async (payload) =>
    jsonResponse<CrawlGraphDetail>(
      await fetch(apiUrl("/crawl-graphs/"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
    )
  )
}

export function useDeleteCrawlGraph() {
  return useGraphMutation<string, void>(async (graphId) => {
    const response = await fetch(apiUrl(`/crawl-graphs/${graphId}`), {
      method: "DELETE",
    })
    if (!response.ok) {
      throw await apiErrorFromResponse(response)
    }
  })
}

export function useCreateCrawlGraphNode(graphId: string) {
  return useGraphMutation<
    { name: string; description: string },
    CrawlGraphNode
  >(async (payload) =>
    jsonResponse(
      await fetch(apiUrl(`/crawl-graphs/${graphId}/nodes`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
    )
  )
}

export function useDeleteCrawlGraphNode(graphId: string) {
  return useGraphMutation<string, void>(async (nodeId) => {
    const response = await fetch(
      apiUrl(`/crawl-graphs/${graphId}/nodes/${nodeId}`),
      { method: "DELETE" }
    )
    if (!response.ok) {
      throw await apiErrorFromResponse(response)
    }
  })
}

export function useUpdateCrawlGraphNode(graphId: string) {
  return useGraphMutation<
    { id: string; name: string; description: string },
    CrawlGraphNode
  >(async ({ id, ...payload }) =>
    jsonResponse(
      await fetch(apiUrl(`/crawl-graphs/${graphId}/nodes/${id}`), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
    )
  )
}

export function useUpdateCrawlGraphNodePosition(graphId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({ nodeId, x, y }: { nodeId: string; x: number; y: number }) =>
      jsonResponse<CrawlGraphNode>(
        await fetch(apiUrl(`/crawl-graphs/${graphId}/nodes/${nodeId}/position`), {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ x, y }),
        })
      ),
    onSuccess: (node) => {
      queryClient.setQueryData<CrawlGraphDetail>([...graphsKey, graphId], (graph) =>
        graph ? { ...graph, nodes: graph.nodes.map((item) => item.id === node.id ? node : item) } : graph
      )
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useSetCrawlGraphRoot(graph: CrawlGraphDetail) {
  return useGraphMutation<string, CrawlGraphDetail>(async (rootNodeId) =>
    jsonResponse(
      await fetch(apiUrl(`/crawl-graphs/${graph.id}`), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: graph.name,
          description: graph.description,
          root_node_id: rootNodeId,
        }),
      })
    )
  )
}

export function useCreateCrawlGraphEdge(graphId: string) {
  return useGraphMutation<
    {
      name: string
      description: string
      source_node_id: string
      target_node_id: string
      sql: string
    },
    CrawlGraphEdge
  >(async (payload) =>
    jsonResponse(
      await fetch(apiUrl(`/crawl-graphs/${graphId}/edges`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
    )
  )
}

export function useDeleteCrawlGraphEdge(graphId: string) {
  return useGraphMutation<string, void>(async (edgeId) => {
    const response = await fetch(
      apiUrl(`/crawl-graphs/${graphId}/edges/${edgeId}`),
      { method: "DELETE" }
    )
    if (!response.ok) {
      throw await apiErrorFromResponse(response)
    }
  })
}

export function useUpdateCrawlGraphEdge(graphId: string) {
  return useGraphMutation<
    {
      id: string
      name: string
      description: string
      source_node_id: string
      target_node_id: string
      sql: string
    },
    CrawlGraphEdge
  >(async ({ id, ...payload }) =>
    jsonResponse(
      await fetch(apiUrl(`/crawl-graphs/${graphId}/edges/${id}`), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
    )
  )
}

export function useTriggerCrawlGraph(graphId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (urls: string[]) =>
      jsonResponse<GraphRunSubmission>(
        await fetch(apiUrl(`/crawl-graphs/${graphId}/runs`), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ urls }),
        })
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["graph-runs"] })
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useGraphRuns() {
  return useQuery({
    queryKey: ["graph-runs"],
    refetchInterval: 5_000,
    queryFn: async () =>
      jsonResponse<GraphRunListResponse>(await fetch(apiUrl("/graph-runs/"))),
  })
}

export function useActiveGraphRuns(graphId: string) {
  return useQuery({
    queryKey: ["graph-runs", "active", graphId],
    refetchInterval: 2_000,
    queryFn: async () => jsonResponse<GraphRunListResponse>(
      await fetch(apiUrl(`/crawl-graphs/${graphId}/runs/active`))
    ),
  })
}

export function useGraphRun(runId: string | null) {
  return useQuery({
    queryKey: ["graph-runs", runId],
    enabled: runId !== null,
    refetchInterval: (query) => {
      const status = (query.state.data as GraphRunRecord | undefined)?.status
      return status === "queued" || status === "running" ? 2_000 : false
    },
    queryFn: async () => jsonResponse<GraphRunRecord>(
      await fetch(apiUrl(`/graph-runs/${runId}`))
    ),
  })
}

export function useCancelGraphRun() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (runId: string) => jsonResponse<GraphRunRecord>(
      await fetch(apiUrl(`/graph-runs/${runId}/cancel`), { method: "POST" })
    ),
    onSuccess: async (run) => {
      queryClient.setQueryData(["graph-runs", run.id], run)
      await queryClient.invalidateQueries({ queryKey: ["graph-runs"] })
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}
