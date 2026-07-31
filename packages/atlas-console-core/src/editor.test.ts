import assert from "node:assert/strict"
import test from "node:test"

import {
  GhostTextEditor,
  type Disposable,
  type InteractiveTerminal,
} from "./editor.js"

class TestTerminal implements InteractiveTerminal {
  output = ""
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
}

test("edits at the cursor before submitting SQL", async () => {
  const terminal = new TestTerminal()
  const editor = new GhostTextEditor(terminal, async () => [])
  const result = editor.readLine("atlas> ", "SELET;")

  terminal.send("\u001b[D")
  terminal.send("\u001b[D")
  terminal.send("C")
  terminal.send("\r")

  assert.equal(await result, "SELECT;")
})

test("collects multiline SQL until a terminating semicolon", async () => {
  const terminal = new TestTerminal()
  const editor = new GhostTextEditor(terminal, async () => [])
  const result = editor.readLine("atlas> ")

  terminal.send("SELECT url")
  terminal.send("\r")
  terminal.send("FROM web.page;")
  terminal.send("\r")

  assert.equal(await result, "SELECT url\nFROM web.page;")
  assert.match(terminal.output, /\.\.\.> /)
})

test("control-d exits from an empty prompt", async () => {
  const terminal = new TestTerminal()
  const editor = new GhostTextEditor(terminal, async () => [])
  const result = editor.readLine("atlas> ")

  terminal.send("\u0004")

  assert.equal(await result, ".exit")
})

test("renders the top completion as ghost text and accepts it with tab", async () => {
  const terminal = new TestTerminal()
  const editor = new GhostTextEditor(
    terminal,
    async () => [
      {
        value: "web.link",
        replaceStart: "SELECT * FROM ".length,
        replaceEnd: "SELECT * FROM web.".length,
        kind: "relation",
        description: "Unique captured content.",
      },
      {
        value: "web.crawls",
        replaceStart: "SELECT * FROM ".length,
        replaceEnd: "SELECT * FROM web.".length,
        kind: "relation",
      },
    ],
    0,
  )
  const result = editor.readLine("atlas> ", "SELECT * FROM web.")

  await new Promise((resolve) => setTimeout(resolve, 5))
  assert.match(terminal.output, /\u001b\[2mlink\u001b\[0m/)
  assert.doesNotMatch(terminal.output, /web\.crawls/)

  terminal.send("\t")
  terminal.send(";")
  terminal.send("\r")

  assert.equal(await result, "SELECT * FROM web.link;")
})

test("keeps a long loaded draft inside a single-line viewport", async () => {
  const terminal = new TestTerminal()
  const draft =
    "SELECT p.hostname, COUNT(*) AS visit_count FROM web.page AS p " +
    "JOIN web.visit AS v ON coalesce(v.effective_url, v.requested_url) = p.url " +
    "GROUP BY p.hostname ORDER BY visit_count DESC;"
  const editor = new GhostTextEditor(terminal, async () => [])
  const result = editor.readLine("atlas> ", draft)

  assert.match(terminal.output, /…/)
  assert.doesNotMatch(terminal.output, /SELECT p\.hostname/)

  terminal.send("\r")
  assert.equal(await result, draft)
})
