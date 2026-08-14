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
  const result = editor.readLine("periplus> ", "SELET;")

  terminal.send("\u001b[D")
  terminal.send("\u001b[D")
  terminal.send("C")
  terminal.send("\r")

  assert.equal(await result, "SELECT;")
})

test("collects multiline SQL until a terminating semicolon", async () => {
  const terminal = new TestTerminal()
  const editor = new GhostTextEditor(terminal, async () => [])
  const result = editor.readLine("periplus> ")

  terminal.send("SELECT requested_url")
  terminal.send("\r")
  terminal.send("FROM web.observation;")
  terminal.send("\r")

  assert.equal(await result, "SELECT requested_url\nFROM web.observation;")
  assert.match(terminal.output, /\.\.\.> /)
})

test("control-d exits from an empty prompt", async () => {
  const terminal = new TestTerminal()
  const editor = new GhostTextEditor(terminal, async () => [])
  const result = editor.readLine("periplus> ")

  terminal.send("\u0004")

  assert.equal(await result, ".exit")
})

test("renders the top completion as ghost text and accepts it with tab", async () => {
  const terminal = new TestTerminal()
  const editor = new GhostTextEditor(
    terminal,
    async () => [
      {
        value: "web.link_occurrence",
        replaceStart: "SELECT * FROM ".length,
        replaceEnd: "SELECT * FROM web.".length,
        kind: "relation",
        description: "Unique captured content.",
      },
      {
        value: "web.observation",
        replaceStart: "SELECT * FROM ".length,
        replaceEnd: "SELECT * FROM web.".length,
        kind: "relation",
      },
    ],
    0,
  )
  const result = editor.readLine("periplus> ", "SELECT * FROM web.")

  await new Promise((resolve) => setTimeout(resolve, 5))
  assert.match(terminal.output, /\u001b\[2mlink_occurrence\u001b\[0m/)
  assert.doesNotMatch(terminal.output, /web\.crawl/)

  terminal.send("\t")
  terminal.send(";")
  terminal.send("\r")

  assert.equal(await result, "SELECT * FROM web.link_occurrence;")
})

test("keeps a long loaded draft inside a single-line viewport", async () => {
  const terminal = new TestTerminal()
  const draft =
    "SELECT requested_url, COUNT(*) AS observation_count FROM web.observation " +
    "GROUP BY requested_url ORDER BY observation_count DESC;"
  const editor = new GhostTextEditor(terminal, async () => [])
  const result = editor.readLine("periplus> ", draft)

  assert.match(terminal.output, /…/)
  assert.doesNotMatch(terminal.output, /SELECT requested_url/)

  terminal.send("\r")
  assert.equal(await result, draft)
})
