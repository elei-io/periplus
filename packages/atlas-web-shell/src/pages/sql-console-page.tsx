import {
  SqlApi,
  SqlConsole,
  WELCOME,
  startProgress,
  type ConsoleResult,
} from "atlas-console-core"
import { Terminal } from "@wterm/react"
import type { WTerm } from "@wterm/dom"
import { useEffect, useState } from "react"

import { apiUrl } from "@/lib/api"
import { renderSqlResult } from "@/lib/sql-output"
import { WtermTerminal } from "@/lib/wterm-terminal"

const PROMPT = "atlas> "
const HISTORY_KEY = "atlas.sql.history"

export function SqlConsolePage() {
  const [terminal] = useState(() => new WtermTerminal())
  const [session] = useState(
    () =>
      new BrowserSqlSession(
        terminal,
        new SqlConsole(
          new SqlApi(new URL(apiUrl("/"), window.location.origin).toString()),
        ),
      ),
  )

  useEffect(() => () => session.close(), [session])

  function ready(instance: WTerm) {
    terminal.attach(instance)
    session.start()
  }

  return (
    <section className="flex h-full min-h-0 w-full flex-col bg-background">
      <Terminal
        className="atlas-wterm min-h-0 flex-1"
        autoResize
        cursorBlink
        onData={(data) => void session.receive(data)}
        onReady={ready}
        onResize={(columns) => terminal.resized(columns)}
        onError={(error) =>
          terminal.write(
            `\r\nError: ${error instanceof Error ? error.message : String(error)}\r\n`,
          )
        }
      />
    </section>
  )
}

class BrowserSqlSession {
  private input = ""
  private history = loadHistory()
  private historyIndex = this.history.length
  private readonly terminal: WtermTerminal
  private readonly sqlConsole: SqlConsole
  private busy = false
  private progress?: BrowserProgress

  constructor(
    terminal: WtermTerminal,
    sqlConsole: SqlConsole,
  ) {
    this.terminal = terminal
    this.sqlConsole = sqlConsole
  }

  start() {
    this.terminal.write(
      `\u001b[2J\u001b[H${WELCOME.replaceAll("\n", "\r\n")}\r\n\r\n`,
    )
    this.prompt()
    this.terminal.focus()
  }

  async receive(data: string): Promise<void> {
    if (this.busy) {
      if (data === "\u0003" && this.sqlConsole.interrupt()) {
        this.progress?.stop()
        this.terminal.write("^C\r\n")
      }
      return
    }
    if (data === "\r") {
      await this.submit()
      return
    }
    if (data === "\t") {
      await this.complete()
      return
    }
    if (data === "\u0003") {
      if (this.sqlConsole.interrupt()) this.terminal.write("^C\r\n")
      this.input = ""
      this.prompt()
      return
    }
    if (data === "\u007f") {
      if (!this.input) return
      this.input = this.input.slice(0, -1)
      this.terminal.write("\b \b")
      return
    }
    if (data === "\u001b[A") {
      this.recall(-1)
      return
    }
    if (data === "\u001b[B") {
      this.recall(1)
      return
    }
    if (/^[^\u0000-\u001f]+$/.test(data)) {
      this.input += data
      this.terminal.write(data)
    }
  }

  close() {
    this.progress?.stop()
    this.sqlConsole.interrupt()
  }

  private async submit() {
    const sql = this.input.trim()
    this.terminal.write("\r\n")
    this.input = ""
    if (!sql) {
      this.prompt()
      return
    }
    this.history.push(sql)
    this.history = this.history.slice(-100)
    this.historyIndex = this.history.length
    persistHistory(this.history)
    this.busy = true
    this.progress = sql.startsWith(".")
      ? undefined
      : new BrowserProgress(this.terminal, "Running query…")
    try {
      const result = await this.sqlConsole.run(sql)
      this.progress?.stop()
      if (result) this.render(result)
    } catch (error) {
      this.progress?.stop()
      if (isAbort(error)) {
        this.terminal.write("\u001b[2mQuery cancelled.\u001b[0m\r\n")
      } else {
        const message = error instanceof Error ? error.message : String(error)
        this.terminal.write(`\u001b[31mError: ${message}\u001b[0m\r\n`)
      }
    } finally {
      this.progress?.stop()
      this.progress = undefined
      this.busy = false
    }
    this.prompt()
  }

  private render(result: ConsoleResult) {
    if (result.kind === "clear") {
      this.terminal.write("\u001b[2J\u001b[H")
    } else if (result.kind === "message") {
      this.terminal.write(`${result.text.replaceAll("\n", "\r\n")}\r\n`)
    } else {
      this.terminal.write(renderSqlResult(result, this.terminal.columns()))
    }
  }

  private async complete() {
    try {
      const items = await this.sqlConsole.complete(this.input)
      if (!items.length) return
      if (items.length === 1) {
        this.insertCompletion(items[0]!)
        return
      }
      const common = commonPrefix(items.map((item) => item.value))
      const typed = this.input.slice(items[0]!.replaceStart)
      if (common.length > typed.length) {
        this.insertCompletion({
          ...items[0]!,
          value: common,
        })
      }
      this.terminal.write(
        `\r\n${items.slice(0, 40).map((item) => item.value).join("  ")}\r\n`,
      )
      this.redraw()
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      this.terminal.write(`\r\n\u001b[31m${message}\u001b[0m\r\n`)
      this.redraw()
    }
  }

  private recall(direction: -1 | 1) {
    this.historyIndex = Math.max(
      0,
      Math.min(this.history.length, this.historyIndex + direction),
    )
    this.input = this.history[this.historyIndex] ?? ""
    this.redraw()
  }

  private insertCompletion(item: {
    value: string
    replaceStart: number
  }) {
    const typed = this.input.slice(item.replaceStart)
    const suffix = item.value.slice(typed.length)
    this.input += suffix
    this.terminal.write(suffix)
  }

  private redraw() {
    this.terminal.write(`\r\u001b[2K${PROMPT}${this.input}`)
  }

  private prompt() {
    this.terminal.write(PROMPT)
  }
}

class BrowserProgress {
  private readonly progress: { stop(): void }

  constructor(
    terminal: WtermTerminal,
    message: string,
  ) {
    this.progress = startProgress(
      ({ symbol, elapsedSeconds }) =>
        terminal.write(
          `\r\u001b[2K\u001b[1;34m${symbol}\u001b[0m ${message} \u001b[2m${elapsedSeconds}s\u001b[0m`,
        ),
      () => terminal.write("\r\u001b[2K"),
    )
  }

  stop() {
    this.progress.stop()
  }
}

function loadHistory(): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(HISTORY_KEY) ?? "[]")
    return Array.isArray(value)
      ? value.filter((item): item is string => typeof item === "string")
      : []
  } catch {
    return []
  }
}

function persistHistory(history: string[]) {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(history))
  } catch {
    // History remains available for this browser session.
  }
}

function commonPrefix(values: string[]): string {
  if (!values.length) return ""
  let prefix = values[0]!
  for (const value of values.slice(1)) {
    while (!value.startsWith(prefix)) prefix = prefix.slice(0, -1)
    if (!prefix) break
  }
  return prefix
}

function isAbort(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError"
}
