#!/usr/bin/env node

import { createInterface } from "node:readline"
import { SqlApi, SqlConsole, WELCOME } from "atlas-console-core"
import { renderConsoleResult, startProgress } from "./render.js"

const apiUrl =
  process.env.ATLAS_API_URL?.trim() || "http://127.0.0.1:8000"
const sqlConsole = new SqlConsole(new SqlApi(apiUrl))

if (process.argv.length > 2) {
  await execute(process.argv.slice(2).join(" "))
} else {
  await interactive()
}

async function interactive(): Promise<void> {
  if (!process.stdin.isTTY || !process.stdout.isTTY) {
    throw new Error("interactive SQL requires a terminal")
  }
  const terminal = createInterface({
    input: process.stdin,
    output: process.stdout,
    historySize: 100,
    prompt: "atlas> ",
    completer(line, callback) {
      void sqlConsole
        .complete(line)
        .then((items) => {
          const prefix = line.match(/[A-Za-z_][A-Za-z0-9_.]*$/)?.[0] ?? ""
          callback(null, [items.map((item) => item.value), prefix])
        })
        .catch(() => callback(null, [[], ""]))
    },
  })
  process.stdout.write(`\u001b[2J\u001b[H${WELCOME}\n\n`)
  terminal.prompt()
  terminal.on("SIGINT", () => {
    if (sqlConsole.interrupt()) {
      process.stdout.write("^C\n")
    } else {
      process.stdout.write("\r\u001b[2K")
      terminal.prompt()
    }
  })
  for await (const line of terminal) {
    const sql = line.trim()
    if (sql) await execute(sql)
    terminal.prompt()
  }
  terminal.close()
}

async function execute(sql: string): Promise<void> {
  const progress = sql.startsWith(".") ? undefined : startProgress("Running query…")
  try {
    const result = await sqlConsole.run(sql)
    progress?.stop()
    if (result) process.stdout.write(renderConsoleResult(result))
  } catch (error) {
    progress?.stop()
    process.stderr.write(
      `Error: ${error instanceof Error ? error.message : String(error)}\n`,
    )
  } finally {
    progress?.stop()
  }
}
