import { createContext, createElement, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react"

import { apiUrl } from "@/lib/api"

export type NodeActivity = {
  request_id: string
  status: string
  url: string
  updated_at: string
  error: string | null
}

export type NodeProgress = {
  kind: "node_progress"
  graph_run_id: string
  node_id: string
  admitted: number
  queued: number
  crawling: number
  awaiting_materializations: number
  evaluating_edges: number
  completed: number
  failed: number
  cancelled: number
  activity: NodeActivity[]
  settled: boolean
}

export type EdgeProgress = {
  kind: "edge_progress"
  graph_run_id: string
  edge_id: string
  evaluations_pending: number
  evaluations_running: number
  evaluations_completed: number
  evaluations_failed: number
  urls_selected: number
  urls_admitted: number
  urls_deduplicated: number
  settled: boolean
}

type ConnectionState = "idle" | "connecting" | "connected" | "reconnecting" | "stale"
type ProgressSnapshot = {
  graph_run_id: string
  nodes: Record<string, NodeProgress>
  edges: Record<string, EdgeProgress>
}
type ProgressContextValue = {
  runId: string | null
  nodes: Record<string, NodeProgress>
  edges: Record<string, EdgeProgress>
  connectionState: ConnectionState
}

const ProgressContext = createContext<ProgressContextValue>({
  runId: null,
  nodes: {},
  edges: {},
  connectionState: "idle",
})

export function GraphProgressProvider({ runId, children }: { runId: string | null; children: ReactNode }) {
  const [snapshot, setSnapshot] = useState<{ runId: string; nodes: Record<string, NodeProgress>; edges: Record<string, EdgeProgress> } | null>(null)
  const [connection, setConnection] = useState<{ runId: string; state: ConnectionState } | null>(null)

  useEffect(() => {
    if (!runId) return
    const source = new EventSource(apiUrl(`/graph-runs/${runId}/events`))
    let staleTimer: number | null = null
    const markAlive = () => {
      setConnection({ runId, state: "connected" })
      if (staleTimer !== null) window.clearTimeout(staleTimer)
      staleTimer = window.setTimeout(() => setConnection({ runId, state: "stale" }), 25_000)
    }
    source.onopen = markAlive
    source.onerror = () => setConnection({ runId, state: "reconnecting" })
    source.addEventListener("heartbeat", markAlive)
    source.addEventListener("progress_snapshot", (event) => {
      markAlive()
      const value = JSON.parse((event as MessageEvent<string>).data) as ProgressSnapshot
      setSnapshot({ runId, nodes: value.nodes, edges: value.edges })
    })
    source.addEventListener("node_progress", (event) => {
      markAlive()
      const value = JSON.parse((event as MessageEvent<string>).data) as NodeProgress
      setSnapshot((current) => ({
        runId,
        nodes: { ...(current?.runId === runId ? current.nodes : {}), [value.node_id]: value },
        edges: current?.runId === runId ? current.edges : {},
      }))
    })
    source.addEventListener("edge_progress", (event) => {
      markAlive()
      const value = JSON.parse((event as MessageEvent<string>).data) as EdgeProgress
      setSnapshot((current) => ({
        runId,
        nodes: current?.runId === runId ? current.nodes : {},
        edges: { ...(current?.runId === runId ? current.edges : {}), [value.edge_id]: value },
      }))
    })
    source.addEventListener("run_settled", () => {
      if (staleTimer !== null) window.clearTimeout(staleTimer)
      setConnection({ runId, state: "idle" })
      source.close()
    })
    return () => {
      if (staleTimer !== null) window.clearTimeout(staleTimer)
      source.close()
    }
  }, [runId])

  const value = useMemo<ProgressContextValue>(() => ({
    runId,
    nodes: snapshot?.runId === runId ? snapshot.nodes : {},
    edges: snapshot?.runId === runId ? snapshot.edges : {},
    connectionState: runId === null
      ? "idle"
      : connection?.runId === runId
        ? connection.state
        : "connecting",
  }), [connection, runId, snapshot])

  return createElement(ProgressContext.Provider, { value }, children)
}

export function useGraphProgressConnection() {
  return useContext(ProgressContext).connectionState
}

export function useNodeProgress(runId: string | null, nodeId: string) {
  const context = useContext(ProgressContext)
  const progress = context.runId === runId ? context.nodes[nodeId] ?? null : null
  const activity = useTransientActivity(progress?.activity ?? [])
  return progress ? { ...progress, activity } : null
}

export function useEdgeProgress(runId: string | null, edgeId: string) {
  const context = useContext(ProgressContext)
  return context.runId === runId ? context.edges[edgeId] ?? null : null
}

function useTransientActivity(activity: NodeActivity[]) {
  const [visible, setVisible] = useState<NodeActivity | null>(null)
  const seen = useRef(new Set<string>())
  const hideTimer = useRef<number | null>(null)
  const replaceTimer = useRef<number | null>(null)
  const pending = useRef<NodeActivity | null>(null)
  const lastReplacementAt = useRef(0)
  useEffect(() => () => {
    if (hideTimer.current !== null) window.clearTimeout(hideTimer.current)
    if (replaceTimer.current !== null) window.clearTimeout(replaceTimer.current)
  }, [])
  useEffect(() => {
    const additions = activity.filter((item) => {
      const identity = `${item.request_id}:${item.status}:${item.updated_at}`
      if (seen.current.has(identity)) return false
      seen.current.add(identity)
      return true
    })
    if (additions.length === 0) return
    pending.current = additions[0]

    const replace = () => {
      const next = pending.current
      pending.current = null
      replaceTimer.current = null
      if (!next) return
      lastReplacementAt.current = performance.now()
      setVisible(next)
      if (hideTimer.current !== null) window.clearTimeout(hideTimer.current)
      hideTimer.current = window.setTimeout(() => setVisible(null), 10_000)
    }

    const elapsed = performance.now() - lastReplacementAt.current
    if (visible === null || elapsed >= 250) {
      if (replaceTimer.current !== null) window.clearTimeout(replaceTimer.current)
      replace()
    } else if (replaceTimer.current === null) {
      replaceTimer.current = window.setTimeout(replace, 250 - elapsed)
    }
  }, [activity, visible])
  return visible ? [visible] : []
}
