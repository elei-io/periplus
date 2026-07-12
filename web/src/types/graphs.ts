export type CrawlGraphNode = {
  id: string
  graph_id: string
  name: string
  description: string | null
  position_x: number | null
  position_y: number | null
  used_at: string | null
  created_at: string
}

export type CrawlGraphEdge = {
  id: string
  graph_id: string
  source_node_id: string
  target_node_id: string
  name: string
  description: string | null
  sql: string
  used_at: string | null
  created_at: string
}

export type CrawlGraphSummary = {
  id: string
  name: string
  description: string | null
  root_node_id: string | null
  created_at: string
}

export type CrawlGraphDetail = {
  id: string
  name: string
  description: string | null
  root_node_id: string | null
  created_at: string
  nodes: CrawlGraphNode[]
  edges: CrawlGraphEdge[]
}

export type CrawlGraphListResponse = {
  items: CrawlGraphSummary[]
  total: number
}

export type GraphRunSubmission = {
  graph_id: string
  run_id: string
  status: "queued"
}

export type GraphRunStatus =
  | "queued"
  | "running"
  | "completed"
  | "completed_with_errors"
  | "failed"
  | "cancelled"

export type GraphRunRecord = {
  id: string
  graph_id: string
  status: GraphRunStatus
  trigger_kind: "manual" | "scheduled"
  trigger_urls: string[]
  request_count: number
  pending_request_count: number
  failed_request_count: number
  created_at: string
  started_at: string | null
  completed_at: string | null
  cancel_requested_at: string | null
  error: string | null
}

export type GraphRunListResponse = {
  items: GraphRunRecord[]
  total: number
}
