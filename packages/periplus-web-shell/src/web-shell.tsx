import {
  formatDuration,
  GhostTextEditor,
  sanitizeTerminalText,
  SqlApi,
  SqlConsole,
  WELCOME,
  startProgress,
} from "periplus-console-core"
import type { WTerm } from "@wterm/dom"
import { Terminal } from "@wterm/react"
import { useEffect, useState } from "react"

import { renderConsoleResult } from "./sql-output.js"
import { WtermTerminal } from "./wterm-terminal.js"

const PROMPT = "periplus> "
const DEFAULT_HISTORY_KEY = "periplus.sql.history"

interface ShellStatus {
  connection: "connecting" | "connected" | "disconnected"
  duckdbVersion?: string
  catalogueBytes?: number
  apiLatencyMilliseconds?: number
  error?: string
}

export interface PeriplusWebShellProps {
  apiBaseUrl: string
  className?: string
  historyKey?: string
  initialSql?: string
}

export function PeriplusWebShell({
  apiBaseUrl,
  className,
  historyKey = DEFAULT_HISTORY_KEY,
  initialSql = "",
}: PeriplusWebShellProps) {
  const [status, setStatus] = useState<ShellStatus>({
    connection: "connecting",
  })
  const [terminal] = useState(() => new WtermTerminal())
  const [session] = useState(() => {
    const sqlConsole = new SqlConsole(
      new SqlApi(new URL(apiBaseUrl, window.location.origin).toString()),
      { history: loadHistory(historyKey) }
    )
    return new BrowserSqlSession(
      terminal,
      sqlConsole,
      historyKey,
      initialSql,
      setStatus
    )
  })

  useEffect(() => () => session.close(), [session])

  function ready(instance: WTerm) {
    terminal.attach(instance)
    session.start()
  }

  return (
    <section
      className={["periplus-web-shell", className].filter(Boolean).join(" ")}
    >
      <Terminal
        className="periplus-wterm"
        autoResize
        cursorBlink
        onData={(data) => session.receive(data)}
        onReady={ready}
        onResize={(columns) => terminal.resized(columns)}
        onError={(error) =>
          terminal.writeRaw(
            `\r\n\u001b[31mError: ${sanitizeTerminalText(
              error instanceof Error ? error.message : String(error)
            )}\u001b[0m\r\n`
          )
        }
      />
      <ShellFooter status={status} />
    </section>
  )
}

class BrowserSqlSession {
  private readonly editor: GhostTextEditor
  private started = false
  private running = false
  private closed = false
  private progress?: BrowserProgress

  constructor(
    private readonly terminal: WtermTerminal,
    private readonly sqlConsole: SqlConsole,
    private readonly historyKey: string,
    private readonly initialSql: string,
    private readonly onStatus: (status: ShellStatus) => void
  ) {
    this.editor = new GhostTextEditor(
      terminal,
      (input, cursor) => sqlConsole.complete(input, cursor),
      75,
      sqlConsole.history
    )
  }

  start(): void {
    if (this.started) return
    this.started = true
    this.terminal.focus()
    void this.run()
  }

  receive(data: string): void {
    if (this.running) {
      if (data === "\u0003" && this.sqlConsole.interrupt()) {
        this.progress?.stop()
        this.terminal.writeRaw("^C\r\n")
      }
      return
    }
    this.terminal.receive(data)
  }

  close(): void {
    if (this.closed) return
    this.closed = true
    this.progress?.stop()
    this.sqlConsole.interrupt()
    this.terminal.receive("\u0003")
    this.terminal.detach()
  }

  private async run(): Promise<void> {
    this.terminal.writeRaw(
      `\u001b[2J\u001b[H${WELCOME.replaceAll("\n", "\r\n")}\r\n\r\n`
    )
    await this.connect()
    let initialInput = this.initialSql
    while (!this.closed) {
      const line = await this.editor.readLine(PROMPT, initialInput)
      initialInput = ""
      persistHistory(this.historyKey, this.sqlConsole.history)
      if (this.closed) return
      const input = line.trim()
      if (!input) continue
      this.running = true
      this.progress = input.startsWith(".")
        ? undefined
        : new BrowserProgress(this.terminal, "Running query…")
      try {
        const result = await this.sqlConsole.run(input)
        this.progress?.stop()
        persistHistory(this.historyKey, this.sqlConsole.history)
        if (result?.kind === "exit") {
          this.closed = true
          this.terminal.writeRaw(
            "\u001b[2mSession closed. Reload to start again.\u001b[0m\r\n"
          )
          return
        }
        if (result) {
          this.terminal.writeRaw(
            renderConsoleResult(result, this.terminal.columns())
          )
        }
      } catch (error) {
        this.progress?.stop()
        if (isAbort(error)) {
          this.terminal.writeRaw(
            "\u001b[2mQuery cancellation requested.\u001b[0m\r\n"
          )
        } else {
          const message = sanitizeTerminalText(
            error instanceof Error ? error.message : String(error)
          )
          this.terminal.writeRaw(`\u001b[31mError: ${message}\u001b[0m\r\n`)
        }
      } finally {
        this.progress?.stop()
        this.progress = undefined
        this.running = false
      }
    }
  }

  private async connect(): Promise<void> {
    this.onStatus({ connection: "connecting" })
    const startedAt = performance.now()
    try {
      const metadata = await this.sqlConsole.metadata()
      if (this.closed) return
      this.onStatus({
        connection: "connected",
        duckdbVersion: metadata.duckdb_version,
        catalogueBytes: metadata.catalogue_bytes,
        apiLatencyMilliseconds: performance.now() - startedAt,
      })
    } catch (error) {
      if (this.closed) return
      this.onStatus({
        connection: "disconnected",
        error: error instanceof Error ? error.message : String(error),
      })
    }
  }
}

class BrowserProgress {
  private readonly progress: { stop(): void }

  constructor(terminal: WtermTerminal, message: string) {
    this.progress = startProgress(
      ({ symbol, elapsedSeconds }) =>
        terminal.writeRaw(
          `\r\u001b[2K\u001b[1;34m${symbol}\u001b[0m ${message} ` +
            `\u001b[2m${elapsedSeconds}s\u001b[0m`
        ),
      () => terminal.writeRaw("\r\u001b[2K")
    )
  }

  stop(): void {
    this.progress.stop()
  }
}

function ShellFooter({ status }: { status: ShellStatus }) {
  return (
    <footer
      className="periplus-web-shell-footer"
      title={status.error}
      aria-live="polite"
    >
      <span>DuckDB {status.duckdbVersion ?? "—"}</span>
      <span>
        catalogue{" "}
        {status.catalogueBytes === undefined
          ? "—"
          : formatBytes(status.catalogueBytes)}
      </span>
      <span className="periplus-web-shell-footer-end">
        API{" "}
        {status.apiLatencyMilliseconds === undefined
          ? status.connection === "disconnected"
            ? "unavailable"
            : "—"
          : formatDuration(status.apiLatencyMilliseconds)}
      </span>
    </footer>
  )
}

function formatBytes(value: number): string {
  if (value < 1_024) return `${value} B`
  const units = ["KiB", "MiB", "GiB", "TiB", "PiB"]
  let scaled = value
  let unit = -1
  do {
    scaled /= 1_024
    unit += 1
  } while (scaled >= 1_024 && unit < units.length - 1)
  return `${scaled.toFixed(scaled >= 10 ? 0 : 1)} ${units[unit]}`
}

function loadHistory(historyKey: string): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(historyKey) ?? "[]")
    return Array.isArray(value)
      ? value
          .filter((item): item is string => typeof item === "string")
          .slice(-100)
      : []
  } catch {
    return []
  }
}

function persistHistory(historyKey: string, history: readonly string[]): void {
  try {
    localStorage.setItem(historyKey, JSON.stringify(history.slice(-100)))
  } catch {
    // History remains available for this browser session.
  }
}

function isAbort(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError"
}
