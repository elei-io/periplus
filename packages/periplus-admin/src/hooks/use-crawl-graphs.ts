import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { apiErrorFromResponse, apiUrl, extractApiError } from "@/lib/api"
import type {
  CrawlGraphDetail,
  CrawlGraphEdge,
  CrawlGraphEdgeInput,
  CrawlGraphListResponse,
  CrawlGraphNode,
  CrawlGraphUpdateInput,
  CrawlConcurrencyLimits,
  GraphRunSubmission,
  GraphRunTrigger,
  GraphRunListResponse,
  GraphRunDetail,
  GraphRunFailureSummary,
  CrawlSchedule,
  CrawlScheduleInput,
  CrawlScheduleListResponse,
  CrawlScheduleResource,
  CrawlScheduleResourceListResponse,
  SchedulePreviewResponse,
  ScheduleTiming,
} from "@/types/graphs"

const graphsKey = ["crawl-plans"] as const

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
        await fetch(apiUrl("/crawl-plans/"))
      ),
  })
}

export function useCrawlGraph(graphId: string | null) {
  return useQuery({
    queryKey: [...graphsKey, graphId],
    enabled: graphId !== null,
    queryFn: async () =>
      jsonResponse<CrawlGraphDetail>(
        await fetch(apiUrl(`/crawl-plans/${graphId}`))
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
  return useGraphMutation<void, CrawlGraphDetail>(async () =>
    jsonResponse<CrawlGraphDetail>(
      await fetch(apiUrl("/crawl-plans/"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      })
    )
  )
}

export function useUpdateCrawlGraph(graphId: string) {
  return useGraphMutation<CrawlGraphUpdateInput, CrawlGraphDetail>(
    async (payload) =>
      jsonResponse<CrawlGraphDetail>(
        await fetch(apiUrl(`/crawl-plans/${graphId}`), {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        })
      )
  )
}

export function useDeleteCrawlGraph() {
  return useGraphMutation<string, void>(async (graphId) => {
    const response = await fetch(apiUrl(`/crawl-plans/${graphId}`), {
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
      await fetch(apiUrl(`/crawl-plans/${graphId}/nodes`), {
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
      apiUrl(`/crawl-plans/${graphId}/nodes/${nodeId}`),
      { method: "DELETE" }
    )
    if (!response.ok) {
      throw await apiErrorFromResponse(response)
    }
  })
}

export function useCreateCrawlGraphEdge(graphId: string) {
  return useGraphMutation<CrawlGraphEdgeInput, CrawlGraphEdge>(
    async (payload) =>
      jsonResponse(
        await fetch(apiUrl(`/crawl-plans/${graphId}/edges`), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        })
      )
  )
}

export function useUpdateCrawlGraphEdge(graphId: string) {
  return useGraphMutation<
    { id: string; payload: CrawlGraphEdgeInput },
    CrawlGraphEdge
  >(async ({ id, payload }) =>
    jsonResponse(
      await fetch(apiUrl(`/crawl-plans/${graphId}/edges/${id}`), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
    )
  )
}

export function useDeleteCrawlGraphEdge(graphId: string) {
  return useGraphMutation<string, void>(async (edgeId) => {
    const response = await fetch(
      apiUrl(`/crawl-plans/${graphId}/edges/${edgeId}`),
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
      await fetch(apiUrl(`/crawl-plans/${graphId}/nodes/${id}`), {
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
    mutationFn: async ({
      nodeId,
      x,
      y,
    }: {
      nodeId: string
      x: number
      y: number
    }) =>
      jsonResponse<CrawlGraphNode>(
        await fetch(
          apiUrl(`/crawl-plans/${graphId}/nodes/${nodeId}/position`),
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ x, y }),
          }
        )
      ),
    onSuccess: (node) => {
      queryClient.setQueryData<CrawlGraphDetail>(
        [...graphsKey, graphId],
        (graph) =>
          graph
            ? {
                ...graph,
                nodes: graph.nodes.map((item) =>
                  item.id === node.id ? node : item
                ),
              }
            : graph
      )
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useSetCrawlGraphRoot(graph: CrawlGraphDetail) {
  return useGraphMutation<string, CrawlGraphDetail>(async (rootNodeId) =>
    jsonResponse(
      await fetch(apiUrl(`/crawl-plans/${graph.id}`), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          slug: graph.slug,
          description: graph.description,
          root_node_id: rootNodeId,
        }),
      })
    )
  )
}

export function useTriggerCrawlGraph(graphId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (payload: GraphRunTrigger) =>
      jsonResponse<GraphRunSubmission>(
        await fetch(apiUrl(`/crawl-plans/${graphId}/runs`), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
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

export function useGraphRunFailureSummary(runId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["graph-runs", runId, "failure-summary"],
    enabled,
    queryFn: async () =>
      jsonResponse<GraphRunFailureSummary>(
        await fetch(apiUrl(`/graph-runs/${runId}/failure-summary`))
      ),
  })
}

export function useCrawlConcurrencyLimits() {
  return useQuery({
    queryKey: ["graph-runs", "capacity"],
    refetchInterval: 5_000,
    queryFn: async () =>
      jsonResponse<CrawlConcurrencyLimits>(
        await fetch(apiUrl("/graph-runs/capacity"))
      ),
  })
}

export function useActiveGraphRuns(graphId: string) {
  return useQuery({
    queryKey: ["graph-runs", "active", graphId],
    refetchInterval: 2_000,
    queryFn: async () =>
      jsonResponse<GraphRunListResponse>(
        await fetch(apiUrl(`/crawl-plans/${graphId}/runs/active`))
      ),
  })
}

export function useGraphRun(runId: string | null) {
  return useQuery({
    queryKey: ["graph-runs", runId],
    enabled: runId !== null,
    refetchInterval: (query) => {
      const status = (query.state.data as GraphRunDetail | undefined)?.status
      return status === "queued" || status === "running" || status === "paused"
        ? 2_000
        : false
    },
    queryFn: async () =>
      jsonResponse<GraphRunDetail>(await fetch(apiUrl(`/graph-runs/${runId}`))),
  })
}

export function useCancelGraphRun() {
  return useGraphRunControl("cancel")
}

export function usePauseGraphRun() {
  return useGraphRunControl("pause")
}

export function useResumeGraphRun() {
  return useGraphRunControl("resume")
}

function useGraphRunControl(action: "cancel" | "pause" | "resume") {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (runId: string) =>
      jsonResponse<GraphRunDetail>(
        await fetch(apiUrl(`/graph-runs/${runId}/${action}`), {
          method: "POST",
        })
      ),
    onSuccess: async (run) => {
      queryClient.setQueryData(["graph-runs", run.id], run)
      await queryClient.invalidateQueries({ queryKey: ["graph-runs"] })
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

const schedulesKey = (graphId: string) =>
  [...graphsKey, graphId, "schedules"] as const

export function useCrawlSchedules(graphId: string) {
  return useQuery({
    queryKey: schedulesKey(graphId),
    refetchInterval: 15_000,
    queryFn: async () =>
      jsonResponse<CrawlScheduleListResponse>(
        await fetch(apiUrl(`/crawl-plans/${graphId}/schedules`))
      ),
  })
}

export function useAllCrawlSchedules() {
  return useQuery({
    queryKey: ["crawl-schedules"],
    refetchInterval: 15_000,
    queryFn: async () =>
      jsonResponse<CrawlScheduleResourceListResponse>(
        await fetch(apiUrl("/crawl-schedules/"))
      ),
  })
}

export function useCrawlSchedule(scheduleId: string) {
  return useQuery({
    queryKey: ["crawl-schedules", scheduleId],
    queryFn: async () =>
      jsonResponse<CrawlScheduleResource>(
        await fetch(apiUrl(`/crawl-schedules/${scheduleId}`))
      ),
  })
}

function useScheduleMutation<TVariables, TResult>(
  graphId: string,
  mutationFn: (variables: TVariables) => Promise<TResult>
) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn,
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: schedulesKey(graphId),
      })
      await queryClient.invalidateQueries({ queryKey: ["crawl-schedules"] })
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function useCreateCrawlSchedule(graphId: string) {
  return useScheduleMutation<CrawlScheduleInput, CrawlSchedule>(
    graphId,
    async (payload) =>
      jsonResponse<CrawlSchedule>(
        await fetch(apiUrl(`/crawl-plans/${graphId}/schedules`), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        })
      )
  )
}

export function useUpdateCrawlSchedule(graphId: string) {
  return useScheduleMutation<
    { scheduleId: string; payload: CrawlScheduleInput },
    CrawlSchedule
  >(graphId, async ({ scheduleId, payload }) =>
    jsonResponse<CrawlSchedule>(
      await fetch(apiUrl(`/crawl-plans/${graphId}/schedules/${scheduleId}`), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
    )
  )
}

export function useSetCrawlScheduleEnabled(graphId: string) {
  return useScheduleMutation<
    { scheduleId: string; enabled: boolean },
    CrawlSchedule
  >(graphId, async ({ scheduleId, enabled }) =>
    jsonResponse<CrawlSchedule>(
      await fetch(
        apiUrl(`/crawl-plans/${graphId}/schedules/${scheduleId}/enabled`),
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled }),
        }
      )
    )
  )
}

export function useDeleteCrawlSchedule(graphId: string) {
  return useScheduleMutation<string, void>(graphId, async (scheduleId) => {
    const response = await fetch(
      apiUrl(`/crawl-plans/${graphId}/schedules/${scheduleId}`),
      { method: "DELETE" }
    )
    if (!response.ok) throw await apiErrorFromResponse(response)
  })
}

export function useRunCrawlScheduleNow(graphId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (scheduleId: string) =>
      jsonResponse<GraphRunSubmission>(
        await fetch(
          apiUrl(`/crawl-plans/${graphId}/schedules/${scheduleId}/run`),
          { method: "POST" }
        )
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["graph-runs"] })
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
}

export function usePreviewCrawlSchedule(graphId: string) {
  return useMutation({
    mutationFn: async (payload: {
      timing: ScheduleTiming
      starts_at: string | null
      ends_at: string | null
      count?: number
    }) =>
      jsonResponse<SchedulePreviewResponse>(
        await fetch(apiUrl(`/crawl-plans/${graphId}/schedules/preview`), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        })
      ),
    onError: (error) => toast.error(extractApiError(error)),
  })
}
