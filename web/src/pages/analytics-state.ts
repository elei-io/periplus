import type {
  AnalyticsEvent,
  AnalyticsQuery,
  AnalyticsState,
  DirectionId,
  DirectionState,
} from "@/types/analytics"

export const initialAnalyticsState: AnalyticsState = {
  error: null,
  phase: "idle",
  plan: null,
  orientationActivities: [],
  orientationQueries: [],
  directions: [],
  running: false,
  summary: "",
}

function updateDirection(
  directions: DirectionState[],
  directionId: DirectionId,
  update: (direction: DirectionState) => DirectionState
) {
  return directions.map((direction) =>
    direction.direction.id === directionId ? update(direction) : direction
  )
}

function updateQuery(
  queries: AnalyticsQuery[],
  callId: string,
  update: (query: AnalyticsQuery) => AnalyticsQuery
) {
  return queries.map((query) =>
    query.callId === callId ? update(query) : query
  )
}

export function applyAnalyticsEvent(
  state: AnalyticsState,
  event: AnalyticsEvent
): AnalyticsState {
  if (event.type === "analysis.started") {
    return { ...initialAnalyticsState, phase: "planning", running: true }
  }
  if (event.type === "orientation.activity" && event.message) {
    return {
      ...state,
      orientationActivities: [...state.orientationActivities, event.message],
    }
  }
  if (event.type === "plan.completed" && event.plan) {
    return {
      ...state,
      phase: "investigating",
      plan: event.plan,
      directions: event.plan.directions.map((direction) => ({
        direction,
        answer: "",
        error: null,
        queries: [],
        status: "waiting",
      })),
    }
  }
  if (event.type === "direction.started" && event.direction_id) {
    return {
      ...state,
      directions: updateDirection(
        state.directions,
        event.direction_id,
        (direction) => ({ ...direction, status: "running" })
      ),
    }
  }
  if (event.type === "query.started" && event.call_id && event.sql) {
    const query: AnalyticsQuery = {
      callId: event.call_id,
      queryId: null,
      sql: event.sql,
      columns: [],
      columnTypes: [],
      rows: [],
      rowCount: 0,
      truncated: false,
      status: "running",
      error: null,
    }
    if (event.scope === "orientation") {
      const existing = state.orientationQueries.findIndex(
        (current) => current.callId === event.call_id
      )
      return {
        ...state,
        orientationQueries:
          existing === -1
            ? [...state.orientationQueries, query]
            : state.orientationQueries.map((current, index) =>
                index === existing ? query : current
              ),
      }
    }
    if (event.scope !== "analysis" || !event.direction_id) return state
    return {
      ...state,
      directions: updateDirection(
        state.directions,
        event.direction_id,
        (direction) => {
          const existing = direction.queries.findIndex(
            (current) => current.callId === event.call_id
          )
          return {
            ...direction,
            queries:
              existing === -1
                ? [...direction.queries, query]
                : direction.queries.map((current, index) =>
                    index === existing ? query : current
                  ),
          }
        }
      ),
    }
  }
  if (event.type === "query.completed" && event.call_id && event.sql) {
    const completed: AnalyticsQuery = {
      callId: event.call_id,
      queryId: event.query_id,
      sql: event.sql,
      columns: event.columns ?? [],
      columnTypes: event.column_types ?? [],
      rows: event.rows ?? [],
      rowCount: event.row_count ?? event.rows?.length ?? 0,
      truncated: event.truncated ?? false,
      status: "completed",
      error: null,
    }
    if (event.scope === "orientation") {
      const exists = state.orientationQueries.some(
        (query) => query.callId === completed.callId
      )
      return {
        ...state,
        orientationQueries: exists
          ? updateQuery(
              state.orientationQueries,
              completed.callId,
              () => completed
            )
          : [...state.orientationQueries, completed],
      }
    }
    if (event.scope !== "analysis" || !event.direction_id) return state
    return {
      ...state,
      directions: updateDirection(
        state.directions,
        event.direction_id,
        (direction) => {
          const exists = direction.queries.some(
            (query) => query.callId === completed.callId
          )
          return {
            ...direction,
            queries: exists
              ? updateQuery(
                  direction.queries,
                  completed.callId,
                  () => completed
                )
              : [...direction.queries, completed],
          }
        }
      ),
    }
  }
  if (event.type === "query.failed" && event.call_id) {
    if (event.scope === "orientation") {
      return {
        ...state,
        orientationQueries: updateQuery(
          state.orientationQueries,
          event.call_id,
          (query) => ({
            ...query,
            status: "failed",
            error: event.message,
          })
        ),
      }
    }
    if (event.scope !== "analysis" || !event.direction_id) return state
    return {
      ...state,
      directions: updateDirection(
        state.directions,
        event.direction_id,
        (direction) => ({
          ...direction,
          queries: updateQuery(direction.queries, event.call_id!, (query) => ({
            ...query,
            status: "failed",
            error: event.message,
          })),
        })
      ),
    }
  }
  if (
    event.type === "direction.completed" &&
    event.direction_id &&
    event.direction_answer !== null
  ) {
    const directions = updateDirection(
      state.directions,
      event.direction_id,
      (direction) => ({
        ...direction,
        answer: event.direction_answer ?? "",
        status: "completed",
      })
    )
    return {
      ...state,
      directions,
      phase: directions.every((direction) =>
        ["completed", "failed"].includes(direction.status)
      )
        ? "synthesizing"
        : state.phase,
    }
  }
  if (event.type === "direction.failed" && event.direction_id) {
    const directions = updateDirection(
      state.directions,
      event.direction_id,
      (direction) => ({
        ...direction,
        error: event.message ?? "This direction could not be completed.",
        status: "failed",
      })
    )
    return {
      ...state,
      directions,
      phase: directions.every((direction) =>
        ["completed", "failed"].includes(direction.status)
      )
        ? "synthesizing"
        : state.phase,
    }
  }
  if (event.type === "summary.delta" && event.delta) {
    return {
      ...state,
      phase: "synthesizing",
      summary: state.summary + event.delta,
    }
  }
  if (event.type === "analysis.completed") {
    return {
      ...state,
      phase: "completed",
      summary: event.summary ?? state.summary,
      running: false,
    }
  }
  if (event.type === "analysis.failed") {
    return {
      ...state,
      error: event.message ?? "Atlas could not complete the analysis.",
      phase: "completed",
      running: false,
    }
  }
  return state
}
