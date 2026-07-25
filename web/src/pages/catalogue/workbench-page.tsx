import {
  AtlasConsole,
  GhostTextEditor,
  HttpAtlasApi,
  type AtomicCommandResult,
  type ConsoleStatus,
} from "@atlas/console-core"
import { Terminal } from "@wterm/react"
import type { WTerm } from "@wterm/dom"
import { useEffect, useState } from "react"

import {
  renderConsoleError,
  renderConsoleAssistantHeading,
  renderConsoleProgress,
  renderConsoleResult,
  renderConsoleWelcome,
} from "@/lib/console-output"
import { apiUrl } from "@/lib/api"
import { WtermTerminal } from "@/lib/wterm-terminal"

const HISTORY_KEY = "atlas.console.history"
const HISTORY_LIMIT = 100

export function CatalogueWorkbenchPage() {
  const [terminal] = useState(() => new WtermTerminal())
  const [atlasConsole] = useState(() => {
    const baseUrl = new URL(apiUrl("/"), window.location.origin).toString()
    const value = new AtlasConsole(new HttpAtlasApi(baseUrl))
    value.history.push(...loadHistory())
    return value
  })
  const [ready, setReady] = useState(false)
  const [status, setStatus] = useState<ConsoleStatus>(
    atlasConsole.status.snapshot()
  )

  useEffect(() => {
    const subscription = atlasConsole.status.subscribe(() => {
      setStatus(atlasConsole.status.snapshot())
    })
    return () => subscription.dispose()
  }, [atlasConsole])

  useEffect(() => {
    if (!ready) return
    let stopped = false
    const editor = new GhostTextEditor(
      terminal,
      (input, cursor) => atlasConsole.complete(input, cursor),
      75,
      atlasConsole.history,
      (input) => atlasConsole.status.updateInput(input)
    )

    async function run() {
      const output = new ConsoleOutput(terminal)
      await atlasConsole.status.connect(AbortSignal.timeout(3_000))
      if (stopped) return
      terminal.writeRaw("\u001b[2J\u001b[H")
      terminal.writeRaw(renderConsoleWelcome(atlasConsole.status.snapshot()))
      let initialInput = new URLSearchParams(window.location.search).get("sql") ?? ""

      while (!stopped) {
        const line = await editor.readLine("atlas> ", initialInput)
        initialInput = ""
        persistHistory(atlasConsole.history)
        if (stopped || line.trim() === ".exit") return
        if (!line.trim()) continue
        try {
          const result = await atlasConsole.execute(line)
          if (!result || stopped) continue
          if (result.kind === "stream") {
            for await (const event of result.events) {
              if (stopped) return
              await output.emit(event)
            }
          } else {
            await output.emit(result)
          }
        } catch (reason) {
          output.stop()
          if (stopped) return
          if (isAbort(reason)) {
            terminal.writeRaw("\u001b[2mQuery cancellation requested.\u001b[0m\r\n")
          } else {
            terminal.writeRaw(renderConsoleError(reason))
          }
        }
      }
      output.stop()
    }

    void run()
    return () => {
      stopped = true
      atlasConsole.interrupt()
      terminal.receive("\u0003")
      atlasConsole.status.dispose()
    }
  }, [atlasConsole, ready, terminal])

  function handleData(data: string) {
    if (data === "\u0003" && atlasConsole.interrupt()) {
      terminal.writeRaw("^C\r\n")
      return
    }
    terminal.receive(data)
  }

  function handleReady(instance: WTerm) {
    terminal.attach(instance)
    setReady(true)
    terminal.focus()
  }

  return (
    <section className="flex h-full min-h-0 w-full flex-col bg-background">
      <Terminal
        className="atlas-wterm min-h-0 flex-1"
        autoResize
        cursorBlink
        onData={handleData}
        onReady={handleReady}
        onResize={(columns) => terminal.resized(columns)}
        onError={(error) => terminal.writeRaw(renderConsoleError(error))}
      />
      <ConsoleFooter status={status} />
    </section>
  )
}

class ConsoleOutput {
  private animation?: ReturnType<typeof setInterval>
  private transientLines = 0
  private frame = 0
  private readonly frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
  private readonly terminal: WtermTerminal

  constructor(terminal: WtermTerminal) {
    this.terminal = terminal
  }

  async emit(result: AtomicCommandResult): Promise<void> {
    if (result.kind === "progress" && result.state === "active") {
      if (result.groupStart) {
        this.terminal.writeRaw(renderConsoleAssistantHeading())
      }
      this.start(result.label)
      return
    }
    this.stop()
    if (result.kind === "navigate") navigate(result.path)
    if (result.kind === "copy") await navigator.clipboard.writeText(result.text)
    this.clearTransient()
    const rendered = renderConsoleResult(result, this.terminal.columns())
    this.terminal.writeRaw(rendered)
    if (result.kind === "table" && result.transient) {
      this.transientLines = rendered.split("\r\n").length - 1
    }
  }

  start(label: string): void {
    this.stop()
    const render = () => {
      const frame = this.frames[this.frame % this.frames.length] ?? "⠋"
      this.terminal.writeRaw(renderConsoleProgress(frame, label))
      this.frame += 1
    }
    render()
    this.animation = setInterval(render, 80)
  }

  stop(): void {
    if (!this.animation) return
    clearInterval(this.animation)
    this.animation = undefined
    this.terminal.writeRaw("\r\u001b[2K")
  }

  private clearTransient(): void {
    if (this.transientLines === 0) return
    this.terminal.writeRaw(
      "\u001b[1A\u001b[2K".repeat(this.transientLines)
    )
    this.transientLines = 0
  }
}

function ConsoleFooter({ status }: { status: ConsoleStatus }) {
  const connection =
    status.connection.state === "connected"
      ? "● connected"
      : status.connection.state === "connecting"
        ? "◌ connecting"
        : "○ disconnected"
  const compiler = compilerLabel(status)
  return (
    <footer className="atlas-console-footer flex h-9 shrink-0 items-center gap-4 border-t px-4 font-mono text-[10px]">
      <span
        className={
          status.connection.state === "connected"
            ? "text-emerald-600 dark:text-emerald-400"
            : status.connection.state === "disconnected"
              ? "text-red-600 dark:text-red-400"
              : undefined
        }
      >
        {connection}
      </span>
      <span>lake {status.lakeSlug ?? "—"}</span>
      <span>schema {status.schemaVersion ?? "—"}</span>
      {compiler && (
        <span className="ml-auto min-w-0 truncate" title={compiler.title}>
          {compiler.label}
        </span>
      )}
    </footer>
  )
}

function compilerLabel(
  status: ConsoleStatus
): { label: string; title?: string } | undefined {
  switch (status.compiler.state) {
    case "idle":
    case "debouncing":
      return undefined
    case "checking":
      return { label: "◌ checking" }
    case "valid":
      return { label: "✓ valid" }
    case "optimized":
      return {
        label: `⚡ optimized${
          status.compiler.rewriteCount > 0
            ? ` · ${status.compiler.rewriteCount} ${
                status.compiler.rewriteCount === 1 ? "rewrite" : "rewrites"
              }`
            : ""
        }`,
      }
    case "unavailable":
      return { label: "compiler unavailable", title: status.compiler.message }
    case "diagnostics": {
      const errors = status.compiler.diagnostics.filter(
        (item) => item.severity === "error"
      )
      const diagnostics = errors.length > 0
        ? errors
        : status.compiler.diagnostics
      const symbol = errors.length > 0 ? "✗" : "⚠"
      if (diagnostics.length === 0) return { label: "✗ invalid" }
      return {
        label:
          diagnostics.length === 1
            ? `${symbol} ${diagnostics[0]?.message ?? "Invalid SQL"}`
            : `${symbol} ${diagnostics.length} ${
                errors.length > 0 ? "errors" : "warnings"
              }`,
        title: diagnostics.map((item) => item.message).join("\n"),
      }
    }
  }
}

function navigate(path: string): void {
  window.history.pushState(null, "", path)
  window.dispatchEvent(new PopStateEvent("popstate"))
}

function loadHistory(): string[] {
  try {
    const value: unknown = JSON.parse(
      window.localStorage.getItem(HISTORY_KEY) ?? "[]"
    )
    if (!Array.isArray(value)) return []
    return value
      .filter((item): item is string => typeof item === "string")
      .slice(-HISTORY_LIMIT)
  } catch {
    return []
  }
}

function persistHistory(history: readonly string[]): void {
  try {
    window.localStorage.setItem(
      HISTORY_KEY,
      JSON.stringify(history.slice(-HISTORY_LIMIT))
    )
  } catch {
    // History remains available for this browser session.
  }
}

function isAbort(reason: unknown): boolean {
  return reason instanceof Error && reason.name === "AbortError"
}
