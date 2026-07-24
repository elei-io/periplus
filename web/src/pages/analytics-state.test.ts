import assert from "node:assert/strict"
import test from "node:test"

import {
  applyAnalyticsEvent,
  initialAnalyticsState,
} from "./analytics-state.ts"
import type {
  AnalysisPlan,
  AnalyticsEvent,
  DirectionId,
} from "../types/analytics.ts"

const plan: AnalysisPlan = {
  category: "Retained crawl volume",
  interpretation: "Measure volume and add context.",
  directions: [
    {
      id: "retained_volume",
      title: "Total crawls",
      objective: "Count retained crawls.",
      rationale: "Direct answer.",
    },
    {
      id: "host_distribution",
      title: "By host",
      objective: "Group crawls by host.",
      rationale: "Distribution context.",
    },
    {
      id: "freshness",
      title: "Freshness",
      objective: "Check latest crawl time.",
      rationale: "Recency context.",
    },
    {
      id: "outcomes",
      title: "Outcome quality",
      objective: "Compare crawl outcomes.",
      rationale: "Quality context.",
    },
  ],
}

function event(
  type: AnalyticsEvent["type"],
  fields: Partial<AnalyticsEvent> = {}
): AnalyticsEvent {
  return {
    type,
    run_id: "run",
    plan: null,
    direction: null,
    direction_id: null,
    direction_answer: null,
    handoff_query: null,
    scope: null,
    call_id: null,
    message: null,
    sql: null,
    query_id: null,
    columns: null,
    column_types: null,
    rows: null,
    row_count: null,
    truncated: null,
    delta: null,
    summary: null,
    ...fields,
  }
}

function beginDirections() {
  let state = applyAnalyticsEvent(
    initialAnalyticsState,
    event("analysis.started")
  )
  state = applyAnalyticsEvent(state, event("plan.completed", { plan }))
  return state
}

test("keeps completed queries inside their distinct directions", () => {
  let state = beginDirections()
  for (const [index, directionId] of (
    [
      "retained_volume",
      "host_distribution",
      "freshness",
      "outcomes",
    ] as DirectionId[]
  ).entries()) {
    state = applyAnalyticsEvent(
      state,
      event("query.started", {
        scope: "analysis",
        direction_id: directionId,
        call_id: `call-${index}`,
        sql: `SELECT ${index}`,
      })
    )
    state = applyAnalyticsEvent(
      state,
      event("query.completed", {
        scope: "analysis",
        direction_id: directionId,
        call_id: `call-${index}`,
        query_id: `query-${index}`,
        sql: `SELECT ${index} LIMIT 201`,
        columns: ["value"],
        column_types: ["INTEGER"],
        rows: [[index]],
        row_count: 1,
      })
    )
  }

  assert.deepEqual(
    state.directions.map((direction) => direction.queries[0]?.rows),
    [[[0]], [[1]], [[2]], [[3]]]
  )
})

test("keeps direction evidence when final synthesis fails", () => {
  let state = beginDirections()
  state = applyAnalyticsEvent(
    state,
    event("query.completed", {
      scope: "analysis",
      direction_id: "retained_volume",
      call_id: "call",
      sql: "SELECT 1 LIMIT 201",
      rows: [],
      row_count: 0,
    })
  )
  state = applyAnalyticsEvent(
    state,
    event("analysis.failed", { message: "Synthesis failed." })
  )

  assert.equal(state.directions[0]?.queries[0]?.status, "completed")
  assert.equal(state.error, "Synthesis failed.")
})

test("retains preliminary catalogue activity and SQL separately", () => {
  let state = applyAnalyticsEvent(
    initialAnalyticsState,
    event("analysis.started")
  )
  state = applyAnalyticsEvent(
    state,
    event("orientation.activity", {
      scope: "orientation",
      message: "Surveyed 8 catalogue relations.",
    })
  )
  state = applyAnalyticsEvent(
    state,
    event("query.started", {
      scope: "orientation",
      call_id: "orientation-query",
      sql: "SELECT COUNT(*) FROM main.crawls",
    })
  )
  state = applyAnalyticsEvent(
    state,
    event("query.completed", {
      scope: "orientation",
      call_id: "orientation-query",
      query_id: "query-orientation",
      sql: "SELECT COUNT(*) FROM main.crawls LIMIT 201",
      columns: ["count_star()"],
      column_types: ["BIGINT"],
      rows: [[4]],
      row_count: 1,
    })
  )

  assert.deepEqual(state.orientationActivities, [
    "Surveyed 8 catalogue relations.",
  ])
  assert.equal(state.orientationQueries[0]?.status, "completed")
  assert.deepEqual(state.orientationQueries[0]?.rows, [[4]])
  assert.deepEqual(state.directions, [])
})

test("moves to synthesis after every planned direction settles", () => {
  let state = beginDirections()
  for (const directionId of [
    "retained_volume",
    "host_distribution",
    "freshness",
    "outcomes",
  ] as DirectionId[]) {
    state = applyAnalyticsEvent(
      state,
      event("direction.completed", {
        direction_id: directionId,
        direction_answer: `${directionId} answer`,
      })
    )
  }

  assert.equal(state.phase, "synthesizing")
  assert.deepEqual(
    state.directions.map((direction) => direction.answer),
    [
      "retained_volume answer",
      "host_distribution answer",
      "freshness answer",
      "outcomes answer",
    ]
  )
})

test("attaches a validated handoff query to its direction", () => {
  let state = beginDirections()
  state = applyAnalyticsEvent(
    state,
    event("handoff.started", {
      direction_id: "retained_volume",
    })
  )
  assert.equal(state.directions[0]?.status, "compiling")

  state = applyAnalyticsEvent(
    state,
    event("handoff.completed", {
      direction_id: "retained_volume",
      handoff_query: {
        title: "Retained crawl count",
        sql: "SELECT COUNT(*) AS retained_crawls FROM main.crawls",
        explanation: "Continue from the retained crawl total.",
        caveats: [],
      },
    })
  )
  state = applyAnalyticsEvent(
    state,
    event("direction.completed", {
      direction_id: "retained_volume",
      direction_answer: "There are four retained crawls.",
    })
  )

  assert.equal(
    state.directions[0]?.handoffQuery?.sql,
    "SELECT COUNT(*) AS retained_crawls FROM main.crawls"
  )
  assert.equal(state.directions[0]?.status, "completed")
})
