import assert from "node:assert/strict"
import test from "node:test"

import { commands } from "./commands.js"

test("the shell exposes clear, exit, and help commands", () => {
  assert.deepEqual(
    commands.all().map((command) => command.name),
    ["clear", "exit", "help"],
  )
})

test("help is generated from the command registry", () => {
  const result = commands.execute(".help")

  assert.equal(result.kind, "message")
  if (result.kind === "message") {
    assert.match(result.text, /\.clear\s+Clear the screen\./)
    assert.match(result.text, /\.exit\s+Exit the console\./)
    assert.match(result.text, /\.help\s+Show available commands\./)
  }
})

test("commands autocomplete from the registry", () => {
  assert.deepEqual(commands.complete(".cl").map((item) => item.value), [".clear"])
  assert.deepEqual(commands.complete(".ex").map((item) => item.value), [".exit"])
})

test("exit requests that the shell close", () => {
  assert.deepEqual(commands.execute(".exit"), { kind: "exit" })
})
