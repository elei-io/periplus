#!/usr/bin/env node

import {
  GhostTextEditor,
  sanitizeTerminalText,
  SqlApi,
  SqlConsole,
  WELCOME,
  type Disposable,
  type InteractiveTerminal,
} from "atlas-console-core"
import { renderConsoleResult, startProgress } from "./render.js"

const apiUrl = process.env.ATLAS_API_URL?.trim() || "http://127.0.0.1:8000"
const sqlConsole = new SqlConsole(new SqlApi(apiUrl))

async function interactive(): Promise<void> {
  if (!process.stdin.isTTY || !process.stdout.isTTY) {
    throw new Error("interactive SQL requires a terminal")
  }
  const terminal = new StdioTerminal()
  const editor = new GhostTextEditor(
    terminal,
    (input, cursor) => sqlConsole.complete(input, cursor),
    75,
    sqlConsole.history
  )
  process.stdin.setRawMode(true)
  process.stdin.resume()
  terminal.writeRaw(`\u001b[2J\u001b[H${WELCOME}\r\n\r\n`)
  try {
    while (true) {
      const line = await editor.readLine("atlas> ")
      const input = line.trim()
      if (!input) continue
      const interrupt = terminal.onData((data) => {
        if (data === "\u0003" && sqlConsole.interrupt()) {
          process.stdout.write("^C\r\n")
        }
      })
      try {
        const outcome = await execute(input)
        if (outcome.exit) return
      } finally {
        interrupt.dispose()
      }
    }
  } finally {
    terminal.dispose()
    process.stdin.setRawMode(false)
    process.stdin.pause()
  }
}

async function execute(sql: string): Promise<{ exit: boolean }> {
  const progress = sql.startsWith(".")
    ? undefined
    : startProgress("Running query…")
  try {
    const result = await sqlConsole.run(sql)
    progress?.stop()
    if (result?.kind === "exit") return { exit: true }
    if (result) {
      process.stdout.write(renderConsoleResult(result))
    }
  } catch (error) {
    progress?.stop()
    const message = `Error: ${sanitizeTerminalText(
      error instanceof Error ? error.message : String(error)
    )}`
    process.stderr.write(
      process.stderr.isTTY ? `\u001b[31m${message}\u001b[0m\n` : `${message}\n`
    )
  } finally {
    progress?.stop()
  }
  return { exit: false }
}

class StdioTerminal implements InteractiveTerminal {
  private readonly listeners = new Set<(data: string) => void>()
  private readonly receive = (data: string | Buffer) => {
    const value = data.toString()
    for (const listener of [...this.listeners]) listener(value)
  }

  constructor() {
    process.stdin.setEncoding("utf8")
    process.stdin.on("data", this.receive)
  }

  writeRaw(value: string): void {
    process.stdout.write(value)
  }

  onData(listener: (data: string) => void): Disposable {
    this.listeners.add(listener)
    return { dispose: () => this.listeners.delete(listener) }
  }

  columns(): number {
    return process.stdout.columns ?? 100
  }

  async copyText(value: string): Promise<void> {
    process.stdout.write(
      `\u001b]52;c;${Buffer.from(value).toString("base64")}\u0007`
    )
  }

  dispose(): void {
    this.listeners.clear()
    process.stdin.off("data", this.receive)
  }
}

if (process.argv.length > 2) {
  await execute(process.argv.slice(2).join(" "))
} else {
  await interactive()
}
