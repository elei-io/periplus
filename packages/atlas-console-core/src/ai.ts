import { sanitizeTerminalText } from "./format.js"
import type { InteractiveTerminal } from "./editor.js"
import type {
  AiAnswer,
  AiEvent,
  AiSqlSuggestion,
} from "./types.js"

const BLUE = "\u001b[1;34m"
const BRIGHT = "\u001b[1m"
const CYAN = "\u001b[36m"
const DIM = "\u001b[2m"
const GREEN = "\u001b[32m"
const RED = "\u001b[31m"
const YELLOW = "\u001b[33m"
const RESET = "\u001b[0m"
const SLOW_QUERY_MILLISECONDS = 5_000
const SQL_PAGE_LINES = 10

export interface AiWorkItem {
  callId: string
  purpose: string
  sql: string
  displaySql: string
  state: "completed" | "failed"
  durationMilliseconds: number
  rowCount?: number
  truncated?: boolean
  error?: string
}

export interface AiCompletion {
  answer: AiAnswer
  suggestions: AiSqlSuggestion[]
  work: AiWorkItem[]
  elapsedMilliseconds: number
}

export async function renderAiEvents(
  terminal: InteractiveTerminal,
  events: AsyncIterable<AiEvent>,
): Promise<AiCompletion> {
  terminal.writeRaw(`\r\n${BLUE}◆ Atlas AI${RESET}\r\n`)
  const startedAt = performance.now()
  const work: AiWorkItem[] = []
  const queryIndexes = new Map<string, number>()
  let activeLines = 0
  let activeStartedAt = 0
  let activeEvent: AiEvent | undefined
  let activeTimer: ReturnType<typeof setInterval> | undefined
  let completion:
    | { answer: AiAnswer; suggestions: AiSqlSuggestion[] }
    | undefined
  let failure: string | undefined

  const clearActive = () => {
    if (activeTimer) clearInterval(activeTimer)
    activeTimer = undefined
    if (!activeLines) return
    for (let line = 0; line < activeLines; line += 1) {
      terminal.writeRaw("\r\u001b[2K")
      if (line < activeLines - 1) terminal.writeRaw("\u001b[1A")
    }
    activeLines = 0
  }
  const renderActive = () => {
    if (!activeEvent) return
    clearActive()
    const label = eventLabel(activeEvent)
    const elapsed = performance.now() - activeStartedAt
    const duration = `${DIM}${formatDuration(elapsed)}${RESET}`
    const lines = [
      `${CYAN}  ◌${RESET} ${label} ${duration}`,
    ]
    if (activeEvent.activity === "query" && activeEvent.display_sql) {
      const width = Math.max(20, (terminal.columns?.() ?? 100) - 6)
      lines.push(
        `${DIM}    ${truncate(
          firstSqlLine(activeEvent.display_sql),
          width,
        )}${RESET}`,
      )
    }
    terminal.writeRaw(lines.join("\r\n"))
    activeLines = lines.length
    activeTimer = setInterval(renderActive, 1_000)
  }
  const completeActive = (event: AiEvent) => {
    clearActive()
    activeEvent = undefined
    const failed = event.type === "tool.failed"
    const symbol = failed
      ? `${RED}  ×${RESET}`
      : `${GREEN}  ✓${RESET}`
    const duration = event.duration_ms ?? 0
    const durationColor =
      event.activity === "query" && duration >= SLOW_QUERY_MILLISECONDS
        ? YELLOW
        : DIM
    const facts: string[] = []
    if (event.activity === "query" && event.row_count !== null) {
      facts.push(
        `${event.row_count}${event.truncated ? "+" : ""} ${
          event.row_count === 1 ? "row" : "rows"
        }`,
      )
    }
    if (failed && event.message) {
      facts.push(truncate(plain(event.message), 72))
    }
    terminal.writeRaw(
      `${symbol} ${eventLabel(event)} ` +
        `${durationColor}${formatDuration(duration)}${RESET}` +
        `${facts.length ? ` ${DIM}· ${facts.join(" · ")}${RESET}` : ""}\r\n`,
    )
  }

  for await (const event of events) {
    if (event.type === "tool.started") {
      activeEvent = event
      activeStartedAt = performance.now()
      if (
        event.activity === "query" &&
        event.call_id &&
        event.sql &&
        event.display_sql
      ) {
        queryIndexes.set(event.call_id, work.length)
        work.push({
          callId: event.call_id,
          purpose: event.purpose ?? "Run catalogue query",
          sql: event.sql,
          displaySql: event.display_sql,
          state: "failed",
          durationMilliseconds: 0,
        })
      }
      renderActive()
    } else if (
      event.type === "tool.completed" ||
      event.type === "tool.failed"
    ) {
      completeActive(event)
      if (event.activity === "query" && event.call_id) {
        const index = queryIndexes.get(event.call_id)
        const item = index === undefined ? undefined : work[index]
        if (item) {
          item.state =
            event.type === "tool.completed" ? "completed" : "failed"
          item.durationMilliseconds = event.duration_ms ?? 0
          item.rowCount = event.row_count ?? undefined
          item.truncated = event.truncated ?? undefined
          item.error =
            event.type === "tool.failed"
              ? event.message ?? "Query failed."
              : undefined
          item.sql = event.sql ?? item.sql
          item.displaySql = event.display_sql ?? item.displaySql
        }
      }
    } else if (event.type === "response.failed") {
      failure = event.message ?? "Atlas AI failed."
    } else if (event.type === "response.completed" && event.response) {
      completion = {
        answer: event.response,
        suggestions: event.suggestions,
      }
    }
  }

  clearActive()
  if (failure) throw new Error(failure)
  if (!completion) {
    throw new Error("Atlas AI closed the response without returning an answer.")
  }
  const result: AiCompletion = {
    ...completion,
    work,
    elapsedMilliseconds: performance.now() - startedAt,
  }
  renderAnswer(terminal, result.answer)
  terminal.writeRaw(`${renderSummary(result)}\r\n\r\n`)
  return result
}

export class AiReviewPicker {
  constructor(private readonly terminal: InteractiveTerminal) {}

  choose(completion: AiCompletion): Promise<string | undefined> {
    if (!completion.suggestions.length && !completion.work.length) {
      return Promise.resolve(undefined)
    }
    let mode: "draft" | "work" | "summary" =
      completion.suggestions.length ? "draft" : "summary"
    let draftIndex = 0
    let workIndex = 0
    let sqlOffset = 0
    let renderedLines = 0
    let status = ""
    let finished = false

    const clear = () => {
      if (!renderedLines) return
      for (let line = 0; line < renderedLines; line += 1) {
        this.terminal.writeRaw("\r\u001b[2K")
        if (line < renderedLines - 1) this.terminal.writeRaw("\u001b[1A")
      }
      renderedLines = 0
    }
    const render = () => {
      clear()
      const width = Math.max(32, (this.terminal.columns?.() ?? 100) - 4)
      const lines =
        mode === "draft"
          ? renderDraft(
              completion.suggestions[draftIndex]!,
              draftIndex,
              completion.suggestions.length,
              width,
              sqlOffset,
              Boolean(completion.work.length),
              status,
            )
          : mode === "work"
            ? renderWork(
                completion.work[workIndex]!,
                workIndex,
                completion.work.length,
                width,
                sqlOffset,
                Boolean(completion.suggestions.length),
                status,
              )
            : [
                `${DIM}W inspect ${completion.work.length} SQL ${
                  completion.work.length === 1 ? "query" : "queries"
                } · Enter/Esc continue${status ? ` · ${status}` : ""}${RESET}`,
              ]
      this.terminal.writeRaw(lines.join("\r\n"))
      renderedLines = lines.length
    }
    const activeSql = (): { compact: string; display: string } | undefined => {
      if (mode === "draft") {
        const suggestion = completion.suggestions[draftIndex]
        return suggestion
          ? { compact: suggestion.sql, display: suggestion.display_sql }
          : undefined
      }
      if (mode === "work") {
        const item = completion.work[workIndex]
        return item
          ? { compact: item.sql, display: item.displaySql }
          : undefined
      }
      return undefined
    }

    render()
    return new Promise((resolve) => {
      const finish = (value?: string) => {
        finished = true
        subscription.dispose()
        clear()
        resolve(value)
      }
      const copyActive = () => {
        const copy = this.terminal.copyText
        const sql = activeSql()
        if (!copy || !sql) {
          status = "copy unavailable"
          render()
          return
        }
        void copy.call(this.terminal, sql.display).then(
          () => {
            if (finished) return
            status = "copied formatted SQL"
            render()
          },
          () => {
            if (finished) return
            status = "copy failed"
            render()
          },
        )
      }
      const handleData = (data: string) => {
        if (data === "\t") {
          if (mode === "draft") {
            draftIndex = (draftIndex + 1) % completion.suggestions.length
          } else if (mode === "work") {
            workIndex = (workIndex + 1) % completion.work.length
          }
          sqlOffset = 0
          status = ""
          render()
        } else if (data === "\u001b[Z") {
          if (mode === "draft") {
            draftIndex =
              (draftIndex - 1 + completion.suggestions.length) %
              completion.suggestions.length
          } else if (mode === "work") {
            workIndex =
              (workIndex - 1 + completion.work.length) % completion.work.length
          }
          sqlOffset = 0
          status = ""
          render()
        } else if (data === "\u001b[B") {
          sqlOffset += SQL_PAGE_LINES
          render()
        } else if (data === "\u001b[A") {
          sqlOffset = Math.max(0, sqlOffset - SQL_PAGE_LINES)
          render()
        } else if (data === "w" || data === "W") {
          if (!completion.work.length) return
          mode =
            mode === "work"
              ? completion.suggestions.length
                ? "draft"
                : "summary"
              : "work"
          sqlOffset = 0
          status = ""
          render()
        } else if (data === "\r" || data === "\n") {
          if (mode === "draft") {
            finish(completion.suggestions[draftIndex]!.sql)
          } else {
            finish()
          }
        } else if (data === "\u001b" || data === "\u0003") {
          finish()
        } else if (data === "c" || data === "C") {
          copyActive()
        }
      }
      const subscription = this.terminal.onData(handleData)
    })
  }
}

function renderAnswer(
  terminal: InteractiveTerminal,
  answer: AiAnswer,
): void {
  const width = Math.max(32, (terminal.columns?.() ?? 100) - 2)
  terminal.writeRaw("\r\n")
  terminal.writeRaw(
    wrapText(plain(answer.conclusion), width)
      .map((line) => `${BRIGHT}${line}${RESET}`)
      .join("\r\n"),
  )
  if (answer.evidence.length) {
    terminal.writeRaw("\r\n\r\n")
    const evidence = answer.evidence.flatMap((value) => {
      const wrapped = wrapText(plain(value), width - 4)
      return wrapped.map((line, index) => `${index ? "    " : "  • "}${line}`)
    })
    terminal.writeRaw(evidence.join("\r\n"))
  }
  if (answer.recommendation) {
    terminal.writeRaw("\r\n\r\n")
    const prefix = "Recommendation: "
    const wrapped = wrapText(
      `${prefix}${plain(answer.recommendation)}`,
      width,
    )
    terminal.writeRaw(
      wrapped
        .map((line, index) =>
          index === 0
            ? `${DIM}${prefix}${RESET}${line.slice(prefix.length)}`
            : line,
        )
        .join("\r\n"),
    )
  }
  terminal.writeRaw("\r\n\r\n")
}

function renderSummary(completion: AiCompletion): string {
  const attempts = completion.work.length
  const failures = completion.work.filter((item) => item.state === "failed").length
  const sqlMilliseconds = completion.work.reduce(
    (total, item) => total + item.durationMilliseconds,
    0,
  )
  const facts = [
    `${attempts} SQL ${attempts === 1 ? "query" : "queries"}`,
    failures ? `${failures} ${failures === 1 ? "retry" : "retries"}` : "",
    attempts ? `${formatDuration(sqlMilliseconds)} SQL` : "",
    `${completion.suggestions.length} ${
      completion.suggestions.length === 1 ? "draft" : "drafts"
    }`,
  ].filter(Boolean)
  return `${DIM}${facts.join(" · ")}${RESET}`
}

function renderDraft(
  suggestion: AiSqlSuggestion,
  index: number,
  total: number,
  width: number,
  offset: number,
  hasWork: boolean,
  status: string,
): string[] {
  const sql = sqlPage(suggestion.display_sql, width, offset)
  const description = wrapText(plain(suggestion.description), width).slice(0, 2)
  return [
    `┌─ ${BLUE}SQL draft ${index + 1}/${total}${RESET} · ${plain(
      suggestion.title,
    )}`,
    ...description.map((line) => `│ ${line}`),
    "│",
    ...sql.lines.map((line) => `│ ${CYAN}${line}${RESET}`),
    ...(sql.note ? [`│ ${DIM}${sql.note}${RESET}`] : []),
    `└─ ${DIM}Tab draft · ↑/↓ SQL · Enter edit${
      hasWork ? " · W work" : ""
    } · C copy · Esc dismiss${status ? ` · ${status}` : ""}${RESET}`,
  ]
}

function renderWork(
  item: AiWorkItem,
  index: number,
  total: number,
  width: number,
  offset: number,
  hasDrafts: boolean,
  status: string,
): string[] {
  const sql = sqlPage(item.displaySql, width, offset)
  const failed = item.state === "failed"
  const state = failed ? `${RED}failed${RESET}` : `${GREEN}completed${RESET}`
  const facts = [
    state,
    formatDuration(item.durationMilliseconds),
    item.rowCount === undefined
      ? ""
      : `${item.rowCount}${item.truncated ? "+" : ""} ${
          item.rowCount === 1 ? "row" : "rows"
        }`,
  ].filter(Boolean)
  return [
    `┌─ ${BLUE}Agent work ${index + 1}/${total}${RESET} · ${plain(item.purpose)}`,
    `│ ${facts.join(` ${DIM}·${RESET} `)}`,
    ...(item.error
      ? wrapText(plain(item.error), width).slice(0, 3).map(
          (line) => `│ ${RED}${line}${RESET}`,
        )
      : []),
    "│",
    ...sql.lines.map((line) => `│ ${CYAN}${line}${RESET}`),
    ...(sql.note ? [`│ ${DIM}${sql.note}${RESET}`] : []),
    `└─ ${DIM}Tab query · ↑/↓ SQL${
      hasDrafts ? " · W draft" : " · W back"
    } · C copy · Esc dismiss${status ? ` · ${status}` : ""}${RESET}`,
  ]
}

function sqlPage(
  value: string,
  width: number,
  requestedOffset: number,
): { lines: string[]; note?: string } {
  const allLines = wrapSql(value, width)
  const maximumOffset = Math.max(0, allLines.length - SQL_PAGE_LINES)
  const offset = Math.min(requestedOffset, maximumOffset)
  const lines = allLines.slice(offset, offset + SQL_PAGE_LINES)
  if (allLines.length <= SQL_PAGE_LINES) return { lines }
  const start = offset + 1
  const end = offset + lines.length
  return {
    lines,
    note: `lines ${start}–${end} of ${allLines.length}`,
  }
}

function eventLabel(event: AiEvent): string {
  if (event.activity === "query") {
    return plain(event.purpose ?? "Run catalogue query")
  }
  return plain(event.tool ?? "Working")
}

function firstSqlLine(value: string): string {
  return (
    sanitizeTerminalText(value, true)
      .split(/\r\n|\r|\n/)
      .map((line) => line.trim())
      .find(Boolean) ?? "SQL"
  )
}

function plain(value: string): string {
  return sanitizeTerminalText(value, true)
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/^\s*[-*]\s+/gm, "")
    .replace(/\r\n|\r|\n/g, " ")
    .replace(/\s+/g, " ")
    .trim()
}

function wrapText(value: string, width: number): string[] {
  const output: string[] = []
  let remaining = value
  if (!remaining) return [""]
  while ([...remaining].length > width) {
    const characters = [...remaining]
    const candidate = characters.slice(0, width + 1).join("")
    let split = candidate.lastIndexOf(" ")
    if (split < Math.floor(width / 2)) split = width
    output.push(candidate.slice(0, split))
    remaining = candidate.slice(split).trimStart() +
      characters.slice(width + 1).join("")
  }
  output.push(remaining)
  return output
}

function wrapSql(value: string, width: number): string[] {
  const output: string[] = []
  const safe = sanitizeTerminalText(value, true)
  for (const sourceLine of safe.split(/\r\n|\r|\n/)) {
    const characters = [...sourceLine]
    if (!characters.length) {
      output.push("")
      continue
    }
    for (let offset = 0; offset < characters.length; offset += width) {
      output.push(characters.slice(offset, offset + width).join(""))
    }
  }
  return output
}

function truncate(value: string, width: number): string {
  const characters = [...value]
  return characters.length <= width
    ? value
    : `${characters.slice(0, Math.max(1, width - 1)).join("")}…`
}

function formatDuration(milliseconds: number): string {
  if (milliseconds < 1_000) return `${Math.max(1, Math.round(milliseconds))}ms`
  return `${(milliseconds / 1_000).toFixed(milliseconds < 10_000 ? 1 : 0)}s`
}
