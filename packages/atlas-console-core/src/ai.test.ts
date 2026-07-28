import assert from "node:assert/strict"
import test from "node:test"

import { AiReviewPicker, renderAiEvents } from "./ai.js"
import type { Disposable, InteractiveTerminal } from "./editor.js"
import type { AiEvent, AiSqlSuggestion } from "./types.js"

class TestTerminal implements InteractiveTerminal {
  output = ""
  copied = ""
  private readonly listeners = new Set<(data: string) => void>()

  writeRaw(value: string): void {
    this.output += value
  }

  onData(listener: (data: string) => void): Disposable {
    this.listeners.add(listener)
    return { dispose: () => this.listeners.delete(listener) }
  }

  send(value: string): void {
    for (const listener of [...this.listeners]) listener(value)
  }

  columns(): number {
    return 100
  }

  async copyText(value: string): Promise<void> {
    this.copied = value
  }
}

const suggestions: AiSqlSuggestion[] = [
  {
    title: "Pages",
    description: "Inspect pages.",
    sql: "SELECT * FROM web.pages LIMIT 10;",
    display_sql: "SELECT *\nFROM web.pages\nLIMIT 10;",
  },
  {
    title: "Visits",
    description: "Inspect visits.",
    sql: "SELECT * FROM web.visits LIMIT 10;",
    display_sql: "SELECT *\nFROM web.visits\nLIMIT 10;",
  },
]

const completion = {
  answer: {
    conclusion: "The catalogue contains retained pages.",
    evidence: ["Ten recent pages are available."],
    recommendation: null,
  },
  suggestions,
  work: [
    {
      callId: "query-1",
      purpose: "Count retained pages",
      sql: "SELECT COUNT(*) FROM web.pages;",
      displaySql: "SELECT COUNT(*)\nFROM web.pages;",
      state: "completed" as const,
      durationMilliseconds: 30_100,
      rowCount: 1,
      truncated: false,
    },
  ],
  elapsedMilliseconds: 31_000,
}

test("the review picker shows formatted drafts and loads compact SQL", async () => {
  const terminal = new TestTerminal()
  const selection = new AiReviewPicker(terminal).choose(completion)

  assert.match(terminal.output, /SQL draft 1\/2/)
  assert.match(terminal.output, /SELECT \*/)
  assert.match(terminal.output, /FROM web\.pages/)
  terminal.send("\t")
  terminal.send("c")
  await new Promise((resolve) => setTimeout(resolve, 0))
  terminal.send("\r")

  assert.equal(terminal.copied, suggestions[1]!.display_sql)
  assert.equal(await selection, suggestions[1]!.sql)
})

test("the review picker exposes the exact query work behind the answer", async () => {
  const terminal = new TestTerminal()
  const selection = new AiReviewPicker(terminal).choose(completion)

  terminal.send("w")

  assert.match(terminal.output, /Agent work 1\/1/)
  assert.match(terminal.output, /Count retained pages/)
  assert.match(terminal.output, /30s/)
  assert.match(terminal.output, /1 row/)
  assert.match(terminal.output, /FROM web\.pages/)
  terminal.send("\u001b")

  assert.equal(await selection, undefined)
})

test("the review picker dismisses without selecting SQL", async () => {
  const terminal = new TestTerminal()
  const selection = new AiReviewPicker(terminal).choose(completion)

  terminal.send("\u001b")

  assert.equal(await selection, undefined)
})

test("AI events render concise answers and preserve a visible SQL ledger", async () => {
  const terminal = new TestTerminal()
  async function* events(): AsyncIterable<AiEvent> {
    yield {
      type: "tool.started",
      run_id: "run",
      call_id: "query",
      tool: "query catalogue",
      activity: "query",
      purpose: "Count retained domains",
      sql: "SELECT COUNT(*) FROM web.pages;",
      display_sql: "SELECT COUNT(*)\nFROM web.pages;",
      row_count: null,
      truncated: null,
      duration_ms: null,
      message: null,
      response: null,
      suggestions: [],
    }
    yield {
      type: "tool.completed",
      run_id: "run",
      call_id: "query",
      tool: "query catalogue",
      activity: "query",
      purpose: "Count retained domains",
      sql: "SELECT COUNT(*) FROM web.pages;",
      display_sql: "SELECT COUNT(*)\nFROM web.pages;",
      row_count: 1,
      truncated: false,
      duration_ms: 30_100,
      message: null,
      response: null,
      suggestions: [],
    }
    yield {
      type: "response.completed",
      run_id: "run",
      call_id: null,
      tool: null,
      activity: null,
      purpose: null,
      sql: null,
      display_sql: null,
      row_count: null,
      truncated: null,
      duration_ms: null,
      message: null,
      response: {
        conclusion: "There are **4,413 domains** in the lake.",
        evidence: ["The count uses `registrable_domain` values."],
        recommendation: "Use this as the current coverage baseline.",
      },
      suggestions,
    }
  }

  const result = await renderAiEvents(terminal, events())

  assert.equal(result.suggestions.length, 2)
  assert.equal(result.work[0]?.displaySql, "SELECT COUNT(*)\nFROM web.pages;")
  assert.match(terminal.output, /Count retained domains/)
  assert.match(terminal.output, /30s/)
  assert.match(terminal.output, /1 row/)
  assert.match(terminal.output, /There are 4,413 domains in the lake\./)
  assert.match(terminal.output, /• The count uses registrable_domain values\./)
  assert.match(terminal.output, /Recommendation:/)
  assert.match(terminal.output, /1 SQL query · 30s SQL · 2 drafts/)
  assert.doesNotMatch(terminal.output, /\*\*/)
})
